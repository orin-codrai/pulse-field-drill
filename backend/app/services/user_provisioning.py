"""Idempotent provisioning юзера и его дефолтных счетов.

Контракт: вызывается ТОЛЬКО из GET /api/me, не из общей dependency.
Provisioning делает writes — `current_user` dependency остаётся read-only.

Защита от race на дефолтных счетах (два параллельных /api/me):
- `accounts_ws_name_uq` partial unique index (workspace_id, name) WHERE archived_at IS NULL.
- `pg_insert(...).on_conflict_do_nothing(index_elements=..., index_where=...)`.

`index_where` обязателен — без него Postgres не сматчит partial unique index
и упадёт с "no unique or exclusion constraint matching the ON CONFLICT
specification".

Personal workspace создаётся ТОЛЬКО brand-new юзеру (нет existing personal).
Soft-deleted юзер пропускается полностью (иначе `delete → /me → restore` плодит
workspace'ы — delete зануляет active_workspace_id, /me видит NULL, создаёт
новый). Existing personal переиспользуется (un-archive если нужно).

Self-healing: при normal call архивируется все active personal'ы кроме
active_workspace_id (cleanup для юзеров уже пострадавших от старого бага).
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, User, Workspace, WorkspaceMember
from app.schemas.user import TelegramUser

# (name, type, icon). initial_balance_minor = 0 — фиксировано.
DEFAULT_ACCOUNTS: list[tuple[str, str, str]] = [
    ("Карта", "card", "💳"),
    ("Наличные", "cash", "💵"),
]


async def ensure_user_provisioned(
    session: AsyncSession, tg_user: TelegramUser
) -> User:
    """Upsert юзера + personal workspace + seed дефолтных счетов. Идемпотентно.

    Один вызов = одна транзакция. Caller отвечает за commit.
    """
    # Сериализуем concurrent first-touch по tg_id: иначе два параллельных
    # /api/me brand-new юзера создали бы два personal workspace в гонке
    # (active_workspace_id check — application-level). Lock держится до конца
    # транзакции; разные юзеры не конфликтуют.
    await session.execute(select(func.pg_advisory_xact_lock(tg_user.id)))

    user_stmt = (
        pg_insert(User)
        .values(
            tg_id=tg_user.id,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            username=tg_user.username,
            language_code=tg_user.language_code,
            is_premium=tg_user.is_premium,
        )
        .on_conflict_do_update(
            index_elements=["tg_id"],
            set_={
                "first_name": tg_user.first_name,
                "last_name": tg_user.last_name,
                "username": tg_user.username,
                "language_code": tg_user.language_code,
                "is_premium": tg_user.is_premium,
                "updated_at": func.now(),
            },
        )
        .returning(User)
    )
    user = (await session.execute(user_stmt)).scalar_one()

    # Soft-deleted юзер: НЕ создавать workspace/accounts. Иначе delete зануляет
    # active_workspace_id → /me видит NULL → плодит новый personal каждый
    # цикл delete+restore. Frontend подхватит deleted_at и направит на /restore.
    if user.deleted_at is not None:
        return user

    # Personal workspace + owner-membership + active_workspace_id.
    # 1) active_workspace_id уже стоит → переиспользуем.
    # 2) NULL, но existing personal найден → переиспользуем (un-archive).
    # 3) Brand new — создаём.
    if user.active_workspace_id is None:
        existing_personal = await session.scalar(
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(
                WorkspaceMember.user_id == user.id,
                Workspace.kind == "personal",
            )
            .order_by(Workspace.id)
            .limit(1)
        )
        if existing_personal is not None:
            if existing_personal.archived_at is not None:
                existing_personal.archived_at = None
            user.active_workspace_id = existing_personal.id
            workspace_id = existing_personal.id
        else:
            ws = Workspace(name="Личный", kind="personal")
            session.add(ws)
            await session.flush()  # нужен ws.id для membership + active_workspace_id
            session.add(
                WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
            )
            user.active_workspace_id = ws.id
            workspace_id = ws.id
    else:
        workspace_id = user.active_workspace_id

    # Self-healing: archive дубликаты active personal'ов (cleanup для юзеров
    # уже пострадавших от старого бага — те, у кого > 1 active personal).
    # active_workspace_id остаётся; дубликаты переходят в archived.
    duplicate_ids = (await session.execute(
        select(Workspace.id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user.id,
            Workspace.kind == "personal",
            Workspace.archived_at.is_(None),
            Workspace.id != workspace_id,
        )
    )).scalars().all()
    if duplicate_ids:
        now = datetime.now(timezone.utc)
        for ws_id in duplicate_ids:
            dup = await session.get(Workspace, ws_id)
            dup.archived_at = now

    for name, type_, icon in DEFAULT_ACCOUNTS:
        await session.execute(
            pg_insert(Account)
            .values(
                workspace_id=workspace_id,
                name=name,
                type=type_,
                initial_balance_minor=0,
                icon=icon,
            )
            .on_conflict_do_nothing(
                index_elements=["workspace_id", "name"],
                index_where=Account.archived_at.is_(None),
            )
        )

    return user
