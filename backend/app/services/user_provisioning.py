"""Idempotent provisioning юзера и его дефолтных счетов.

Контракт: вызывается ТОЛЬКО из GET /api/me, не из общей dependency.
Provisioning делает writes — `current_user` dependency остаётся read-only.

Защита от race на дефолтных счетах (два параллельных /api/me):
- `accounts_ws_name_uq` partial unique index (workspace_id, name) WHERE archived_at IS NULL.
- `pg_insert(...).on_conflict_do_nothing(index_elements=..., index_where=...)`.

`index_where` обязателен — без него Postgres не сматчит partial unique index
и упадёт с "no unique or exclusion constraint matching the ON CONFLICT
specification".

Personal workspace создаётся идемпотентно через проверку active_workspace_id.
Гонка на самом первом concurrent /api/me brand-new юзера (оба создадут personal
workspace) принята для v1: /api/me — редкий touchpoint, юзеров двое.
"""

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

    # Personal workspace + owner-membership + active_workspace_id.
    # Идемпотентно: если active_workspace_id уже стоит — переиспользуем.
    if user.active_workspace_id is None:
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
