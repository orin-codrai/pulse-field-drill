"""Юзер endpoints: provisioning, registration, soft-delete, restore.

Все depend'ят от `current_user` напрямую (не `active_user`), чтобы
`/me/restore` мог работать на soft-deleted юзере — иначе круговая 410.
Защита от soft-deleted на других endpoint'ах — через `active_user` /
`current_workspace` /  `registered_user` в их роутерах.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, tg_user_from_auth
from app.db.session import get_session
from app.models import User, Workspace, WorkspaceMember
from app.schemas.user import MeOut, RegistrationBody, TelegramUser
from app.services.user_provisioning import ensure_user_provisioned

router = APIRouter()

# Soft-delete окно до hard-purge. Manual CLI запускает services.purge.
PURGE_AFTER_DAYS = 30


def _build_me_out(user: User, tg_user: TelegramUser) -> MeOut:
    """MF14-6 + C14-5: явное construction, НЕ from_attributes. Иначе
    SQLAlchemy подтянет User.id в `id` (а нам нужен tg_id для frontend
    backward compat).

    MF16-1 canary `test_me_out_helper_covers_all_fields` проверяет
    через `model_dump(exclude_unset=True)`, что helper передал ВСЕ
    поля MeOut explicit (а не оставил defaults).
    """
    return MeOut(
        id=tg_user.id,                              # tg_id
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
        username=tg_user.username,
        language_code=tg_user.language_code,
        is_premium=tg_user.is_premium,
        photo_url=tg_user.photo_url,
        internal_id=user.id,                        # MF14-6 renamed
        active_workspace_id=user.active_workspace_id,
        display_name=user.display_name,
        email=user.email,
        consent_at=user.consent_at,
        deleted_at=user.deleted_at,
        registration_required=(
            user.display_name is None or user.consent_at is None
        ),
    )


@router.get("/me", response_model=MeOut)
async def me(
    tg_user: TelegramUser = Depends(tg_user_from_auth),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    """Первый touchpoint юзера. Idempotent provisioning + возврат MeOut.

    PIN-C: soft-deleted юзер НЕ восстанавливается автоматически. Возвращаем
    MeOut с deleted_at != null → фронт редиректит на /restore. Чтение
    GET /me разрешено даже soft-deleted (иначе нет пути увидеть restore-экран).
    """
    user = await ensure_user_provisioned(session, tg_user)
    await session.commit()
    return _build_me_out(user, tg_user)


@router.post("/me/register", response_model=MeOut)
async def register_me(
    body: RegistrationBody,
    tg_user: TelegramUser = Depends(tg_user_from_auth),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    """Дозаполнение профиля. consent=False отбит на уровне Pydantic
    (Literal[True]). Идемпотентно: повторный вызов перезаписывает поля,
    consent_at ставится один раз (первый POST)."""
    user.display_name = body.display_name
    user.email = body.email
    if user.consent_at is None:
        user.consent_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)
    return _build_me_out(user, tg_user)


@router.post("/me/delete", response_model=MeOut)
async def soft_delete_me(
    tg_user: TelegramUser = Depends(tg_user_from_auth),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    """Soft-delete: deleted_at=now, active_workspace_id=NULL, архив personal
    workspace'ов юзера, убрать membership из shared (shared workspace и его
    данные остаются — переживают удаление любого участника, ADR-0009 §5).

    Idempotent: повторный вызов на уже-soft-deleted → no-op (возврат
    текущего state)."""
    if user.deleted_at is not None:
        return _build_me_out(user, tg_user)

    now = datetime.now(timezone.utc)
    user.deleted_at = now
    user.active_workspace_id = None

    # Архивация personal workspace'ов (MF14-3: множественное число — в теории
    # может быть >1; provisioning ставит ровно один, но invariant БД не
    # гарантирует, делаем безопасный bulk).
    personal_ws_ids = (await session.execute(
        select(Workspace.id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user.id,
            Workspace.kind == "personal",
            Workspace.archived_at.is_(None),
        )
    )).scalars().all()
    for ws_id in personal_ws_ids:
        ws = await session.get(Workspace, ws_id)
        ws.archived_at = now

    # Membership из shared workspaces убрать (shared переживает; B продолжает
    # пользоваться без A). PIN-G: при restore membership в shared НЕ
    # восстанавливается — для shared B должен заново пригласить.
    await session.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.user_id == user.id,
            WorkspaceMember.workspace_id.in_(
                select(Workspace.id).where(Workspace.kind == "shared")
            ),
        )
    )

    await session.commit()
    await session.refresh(user)
    return _build_me_out(user, tg_user)


@router.post("/me/restore", response_model=MeOut)
async def restore_me(
    tg_user: TelegramUser = Depends(tg_user_from_auth),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    """Restore soft-deleted юзера в пределах PURGE_AFTER_DAYS=30.

    Восстанавливает personal workspace (un-archive ALL — MF14-3 множественное)
    + active_workspace_id ← первый из них. PIN-G: shared НЕ восстанавливается;
    для shared B должен заново пригласить.
    """
    if user.deleted_at is None:
        return _build_me_out(user, tg_user)

    now = datetime.now(timezone.utc)
    if (now - user.deleted_at).days >= PURGE_AFTER_DAYS:
        # Hard-purge должен был сработать через CRON/CLI; defence-in-depth.
        raise HTTPException(
            status.HTTP_410_GONE,
            "account beyond restore window; data already purged",
        )

    user.deleted_at = None

    # Un-archive все personal workspace'ы.
    personal_ws_rows = (await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user.id,
            Workspace.kind == "personal",
        )
        .order_by(Workspace.id)
    )).scalars().all()
    for ws in personal_ws_rows:
        if ws.archived_at is not None:
            ws.archived_at = None
    # active_workspace_id ← первый personal (invariant: provisioning один personal).
    if personal_ws_rows:
        user.active_workspace_id = personal_ws_rows[0].id

    await session.commit()
    await session.refresh(user)
    return _build_me_out(user, tg_user)
