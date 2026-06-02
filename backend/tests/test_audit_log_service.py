"""Unit-тесты services/audit_log.py. Pure session-level, без HTTP."""

import pytest

from app.models import Account, Transaction
from app.schemas.user import TelegramUser
from app.services.audit_log import log_action
from app.services.user_provisioning import ensure_user_provisioned


async def _setup_user(db_session):
    user = await ensure_user_provisioned(
        db_session, TelegramUser(id=80001, first_name="Auditor")
    )
    await db_session.commit()
    return user


async def test_log_action_creates_row_with_actor_name(db_session):
    user = await _setup_user(db_session)
    # Простая tx прямо в session (минимально валидная: expense).
    from sqlalchemy import select
    acc = await db_session.scalar(
        select(Account).where(Account.workspace_id == user.active_workspace_id).limit(1)
    )
    from app.models import Category
    cat = await db_session.scalar(
        select(Category).where(
            Category.workspace_id.is_(None), Category.kind == "expense"
        ).limit(1)
    )
    tx = Transaction(
        workspace_id=user.active_workspace_id,
        kind="expense",
        amount_minor=1000,
        from_account_id=acc.id,
        category_id=cat.id,
    )
    db_session.add(tx)
    await db_session.flush()

    row = await log_action(
        db_session,
        workspace_id=user.active_workspace_id,
        actor=user,
        entity_type="transaction",
        entity_id=tx.id,
        action="create",
        entity=tx,
    )
    await db_session.commit()

    assert row.workspace_id == user.active_workspace_id
    assert row.actor_user_id == user.id
    assert row.entity_type == "transaction"
    assert row.entity_id == tx.id
    assert row.action == "create"
    snapshot = row.snapshot_json
    assert snapshot["after"]["kind"] == "expense"
    assert snapshot["after"]["amount_minor"] == 1000
    # actor_name_snapshot — fallback chain. У нового юзера display_name=None,
    # first_name='Auditor' → fallback на first_name.
    assert snapshot["actor_name_snapshot"] == "Auditor"


async def test_log_action_actor_name_uses_display_name_when_set(db_session):
    user = await _setup_user(db_session)
    user.display_name = "Custom Display"
    await db_session.commit()

    acc = user.active_workspace_id  # placeholder для compactness
    from sqlalchemy import select
    acc_obj = await db_session.scalar(
        select(Account).where(Account.workspace_id == acc).limit(1)
    )
    row = await log_action(
        db_session,
        workspace_id=acc,
        actor=user,
        entity_type="account",
        entity_id=acc_obj.id,
        action="update",
        entity=acc_obj,
    )
    await db_session.commit()
    assert row.snapshot_json["actor_name_snapshot"] == "Custom Display"


async def test_log_action_raises_on_unknown_entity_type(db_session):
    user = await _setup_user(db_session)
    with pytest.raises(ValueError, match="unknown entity_type"):
        await log_action(
            db_session,
            workspace_id=user.active_workspace_id,
            actor=user,
            entity_type="envelope",
            entity_id=999,
            action="create",
            entity=None,
        )


async def test_log_action_raises_on_unknown_action(db_session):
    user = await _setup_user(db_session)
    from sqlalchemy import select
    acc = await db_session.scalar(
        select(Account).where(Account.workspace_id == user.active_workspace_id).limit(1)
    )
    with pytest.raises(ValueError, match="unknown audit action"):
        await log_action(
            db_session,
            workspace_id=user.active_workspace_id,
            actor=user,
            entity_type="account",
            entity_id=acc.id,
            action="archive",  # не в enum
            entity=acc,
        )
