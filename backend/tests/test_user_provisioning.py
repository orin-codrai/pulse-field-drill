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

from app.models import Account, User, Workspace
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
            select(Account.name)
            .where(Account.workspace_id == user.active_workspace_id)
            .order_by(Account.id)
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
        select(func.count(Account.id)).where(
            Account.workspace_id == users[0].active_workspace_id
        )
    )
    assert n_accounts == 2


async def test_soft_deleted_provision_does_not_create_workspace(
    db_session: AsyncSession,
):
    """Regression: delete + /me + restore плодил personal workspaces.

    Старое поведение: delete зануляет active_workspace_id; /me видит NULL
    и создаёт NEW personal каждый раз. Restore потом un-archive'ит все →
    +1 workspace на цикл.

    Fix: для soft-deleted (deleted_at IS NOT NULL) provisioning skip'aет
    создание workspace/accounts. Frontend подхватит deleted_at и направит
    на /restore.
    """
    from datetime import datetime, timezone

    # Brand-new юзер: 1 personal.
    user = await ensure_user_provisioned(db_session, make_tg_user())
    await db_session.commit()
    initial_ws_id = user.active_workspace_id
    assert initial_ws_id is not None

    # Симулируем soft-delete вручную: zанулить active_workspace_id, set deleted_at.
    user.deleted_at = datetime.now(timezone.utc)
    user.active_workspace_id = None
    await db_session.commit()

    # /me на soft-deleted: provisioning skip'aет workspace creation.
    user2 = await ensure_user_provisioned(db_session, make_tg_user())
    await db_session.commit()
    assert user2.active_workspace_id is None  # не создал новый
    assert user2.deleted_at is not None

    # Personal workspace всё ещё 1 (тот, что создан в brand-new).
    n_personal = await db_session.scalar(
        select(func.count(Workspace.id)).where(Workspace.kind == "personal")
    )
    assert n_personal == 1


async def test_existing_personal_reused_when_active_workspace_id_null(
    db_session: AsyncSession,
):
    """После restore active_workspace_id=ws.id; но если что-то занулило
    active_workspace_id у НЕ-deleted юзера — provisioning не создаёт новый,
    а переиспользует existing personal (un-archive если archived)."""
    from datetime import datetime, timezone

    user = await ensure_user_provisioned(db_session, make_tg_user())
    await db_session.commit()
    original_ws_id = user.active_workspace_id

    # Симулируем непредвиденное состояние: archived personal, active=NULL,
    # deleted_at=NULL (не soft-deleted).
    user.active_workspace_id = None
    ws = await db_session.get(Workspace, original_ws_id)
    ws.archived_at = datetime.now(timezone.utc)
    await db_session.commit()

    # Второй /me: должен переиспользовать original personal (un-archive).
    user2 = await ensure_user_provisioned(db_session, make_tg_user())
    await db_session.commit()
    assert user2.active_workspace_id == original_ws_id
    ws_refresh = await db_session.get(Workspace, original_ws_id)
    assert ws_refresh.archived_at is None
    # НЕ создан второй personal.
    n_personal = await db_session.scalar(
        select(func.count(Workspace.id)).where(Workspace.kind == "personal")
    )
    assert n_personal == 1


async def test_self_heal_archives_duplicate_personals(
    db_session: AsyncSession,
):
    """Cleanup для юзеров уже пострадавших от старого бага: при /me
    дубликаты active personal'ов архивируются, остаётся только active."""
    from app.models import WorkspaceMember

    user = await ensure_user_provisioned(db_session, make_tg_user())
    await db_session.commit()
    keep_id = user.active_workspace_id

    # Симулируем накопленные дубликаты (как было бы в legacy state).
    extras = []
    for i in range(2):
        dup = Workspace(name=f"Личный {i}", kind="personal")
        db_session.add(dup)
        await db_session.flush()
        db_session.add(
            WorkspaceMember(workspace_id=dup.id, user_id=user.id, role="owner")
        )
        extras.append(dup.id)
    await db_session.commit()

    # До /me: 3 active personal'a.
    n_active_before = await db_session.scalar(
        select(func.count(Workspace.id)).where(
            Workspace.kind == "personal",
            Workspace.archived_at.is_(None),
        )
    )
    assert n_active_before == 3

    # /me: self-heal архивирует дубликаты.
    await ensure_user_provisioned(db_session, make_tg_user())
    await db_session.commit()

    n_active_after = await db_session.scalar(
        select(func.count(Workspace.id)).where(
            Workspace.kind == "personal",
            Workspace.archived_at.is_(None),
        )
    )
    assert n_active_after == 1
    keep_ws = await db_session.get(Workspace, keep_id)
    assert keep_ws.archived_at is None
    for extra_id in extras:
        extra_ws = await db_session.get(Workspace, extra_id)
        assert extra_ws.archived_at is not None


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
    """Параллельные /api/me от одного tg_id → ровно 1 user, 1 workspace, 2 accounts.

    Защита: advisory-lock по tg_id сериализует first-touch (один personal
    workspace), а `accounts_ws_name_uq` + ON CONFLICT DO NOTHING страхует счета.
    """

    async def test_two_concurrent_provisions_give_exactly_two_accounts(
        self, test_engine
    ):
        # Ручной cleanup: класс с no_rollback, fixture не truncate'ит.
        from sqlalchemy import text

        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE audit_log, workspace_invites, envelope_entries, "
                    "transactions, planned_operations, receipts, budgets, "
                    "envelopes, accounts, categories, workspace_members, "
                    "workspaces, users RESTART IDENTITY CASCADE"
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
            n_workspaces = await s.scalar(select(func.count(Workspace.id)))
            n_accounts = await s.scalar(select(func.count(Account.id)))
        assert n_users == 1
        assert n_workspaces == 1
        assert n_accounts == 2
