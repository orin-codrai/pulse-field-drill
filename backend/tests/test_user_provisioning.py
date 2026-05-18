"""Tests for ensure_user_provisioned.

Что покрываем:
- Первый вызов: создаёт user + 2 default accounts.
- Повторный вызов: обновляет профиль, accounts НЕ дублируются.
- ON CONFLICT не падает (отрицательный тест на missing index_where).
- TestConcurrency: два параллельных вызова от одного tg_id → ровно 2 accounts.
"""

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Account, User
from app.schemas.user import TelegramUser
from app.services.user_provisioning import ensure_user_provisioned


def make_tg_user(**overrides) -> TelegramUser:
    base = {
        "id": 42,
        "first_name": "Orrin",
        "last_name": "Test",
        "username": "orrin_test",
        "language_code": "ru",
        "is_premium": False,
    }
    base.update(overrides)
    return TelegramUser(**base)


async def test_first_call_creates_user_and_default_accounts(db_session: AsyncSession):
    tg = make_tg_user()
    user = await ensure_user_provisioned(db_session, tg)
    await db_session.commit()

    assert user.tg_id == 42
    assert user.first_name == "Orrin"

    names = (
        await db_session.execute(
            select(Account.name).where(Account.user_id == user.id).order_by(Account.id)
        )
    ).scalars().all()
    assert list(names) == ["Карта", "Наличные"]


async def test_second_call_updates_profile_no_duplicate_accounts(
    db_session: AsyncSession,
):
    await ensure_user_provisioned(db_session, make_tg_user())
    await db_session.commit()

    # Mutated profile — should upsert.
    await ensure_user_provisioned(
        db_session,
        make_tg_user(first_name="Updated", is_premium=True),
    )
    await db_session.commit()

    users = (await db_session.execute(select(User))).scalars().all()
    assert len(users) == 1
    assert users[0].first_name == "Updated"
    assert users[0].is_premium is True

    n_accounts = await db_session.scalar(
        select(func.count(Account.id)).where(Account.user_id == users[0].id)
    )
    assert n_accounts == 2


async def test_on_conflict_with_partial_index_works(db_session: AsyncSession):
    """Регрессионный: если в коде потерять `index_where`, второй вызов упадёт
    `InvalidColumnReference: there is no unique or exclusion constraint
    matching the ON CONFLICT specification`. Эта проверка стережёт.
    """
    await ensure_user_provisioned(db_session, make_tg_user())
    await db_session.commit()
    # Этот вызов и есть тест — он не должен поднять ProgrammingError/InvalidColumnReference.
    await ensure_user_provisioned(db_session, make_tg_user())
    await db_session.commit()
    # И финальное число accounts всё ещё ровно 2.
    n = await db_session.scalar(select(func.count(Account.id)))
    assert n == 2


@pytest.mark.no_rollback
class TestConcurrency:
    """Параллельные /api/me от одного tg_id → ровно 2 accounts.

    Защита: partial unique index `accounts_user_name_uq` + ON CONFLICT DO NOTHING.
    Без него race window между SELECT count и INSERT дал бы 4 строки.
    """

    async def test_two_concurrent_provisions_give_exactly_two_accounts(
        self, test_engine
    ):
        # Ручной cleanup: класс с no_rollback, fixture не truncate'ит.
        from sqlalchemy import text

        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE transactions, receipts, budgets, goals, accounts, users "
                    "RESTART IDENTITY CASCADE"
                )
            )

        SessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)
        tg = make_tg_user(id=99)

        async def one_call():
            async with SessionLocal() as s:
                await ensure_user_provisioned(s, tg)
                await s.commit()

        await asyncio.gather(one_call(), one_call())

        async with SessionLocal() as s:
            n_users = await s.scalar(select(func.count(User.id)))
            n_accounts = await s.scalar(
                select(func.count(Account.id)).join(User, Account.user_id == User.id)
            )
        assert n_users == 1
        assert n_accounts == 2
