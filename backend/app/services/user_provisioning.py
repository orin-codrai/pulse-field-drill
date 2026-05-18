"""Idempotent provisioning юзера и его дефолтных счетов.

Контракт: вызывается ТОЛЬКО из GET /api/me, не из общей dependency.
Provisioning делает writes — `current_user` dependency остаётся read-only.

Защита от race (два параллельных /api/me от одного юзера):
- `accounts_user_name_uq` partial unique index (user_id, name) WHERE archived_at IS NULL.
- `pg_insert(...).on_conflict_do_nothing(index_elements=..., index_where=...)`.

`index_where` обязателен — без него Postgres не сматчит partial unique index
и упадёт с "no unique or exclusion constraint matching the ON CONFLICT
specification".
"""

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, User
from app.schemas.user import TelegramUser

# (name, type, icon). initial_balance_minor = 0 — фиксировано.
DEFAULT_ACCOUNTS: list[tuple[str, str, str]] = [
    ("Карта", "card", "💳"),
    ("Наличные", "cash", "💵"),
]


async def ensure_user_provisioned(
    session: AsyncSession, tg_user: TelegramUser
) -> User:
    """Upsert юзера + seed дефолтных счетов. Идемпотентно.

    Один вызов = одна транзакция. Caller отвечает за commit.
    """
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

    for name, type_, icon in DEFAULT_ACCOUNTS:
        await session.execute(
            pg_insert(Account)
            .values(
                user_id=user.id,
                name=name,
                type=type_,
                initial_balance_minor=0,
                icon=icon,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "name"],
                index_where=Account.archived_at.is_(None),
            )
        )

    return user
