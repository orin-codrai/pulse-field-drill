"""services/purge.py:hard_purge_user — manual CLI операция."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AuditLog,
    Transaction,
    User,
    Workspace,
    WorkspaceMember,
)
from app.routers.me import PURGE_AFTER_DAYS
from app.schemas.user import TelegramUser
from app.services.purge import hard_purge_user
from app.services.user_provisioning import ensure_user_provisioned


async def test_hard_purge_user_at_29_days_raises(db_session):
    """C16-2 boundary: < 30 дней → ValueError."""
    user = await ensure_user_provisioned(
        db_session, TelegramUser(id=99001, first_name="Test")
    )
    user.deleted_at = datetime.now(timezone.utc) - timedelta(days=29)
    await db_session.commit()
    with pytest.raises(ValueError, match="purge window"):
        await hard_purge_user(db_session, user)


async def test_hard_purge_user_at_exactly_30_days_passes_guard(db_session):
    """C16-2 boundary: ровно 30 дней → проходит."""
    user = await ensure_user_provisioned(
        db_session, TelegramUser(id=99002, first_name="Test")
    )
    user.deleted_at = datetime.now(timezone.utc) - timedelta(days=PURGE_AFTER_DAYS)
    await db_session.commit()
    # Не должно бросить.
    await hard_purge_user(db_session, user)
    # User удалён.
    refreshed = await db_session.scalar(
        select(User).where(User.id == user.id)
    )
    assert refreshed is None


async def test_hard_purge_user_not_soft_deleted_raises(db_session):
    """Guard: user.deleted_at IS NULL → ValueError."""
    user = await ensure_user_provisioned(
        db_session, TelegramUser(id=99003, first_name="Live")
    )
    await db_session.commit()
    with pytest.raises(ValueError, match="non-deleted"):
        await hard_purge_user(db_session, user)


async def test_hard_purge_removes_personal_data(db_session):
    user = await ensure_user_provisioned(
        db_session, TelegramUser(id=99010, first_name="Test")
    )
    # Создаём какие-то данные в personal.
    from app.models import Category
    sys_cat = await db_session.scalar(
        select(Category).where(Category.workspace_id.is_(None)).limit(1)
    )
    acc = await db_session.scalar(
        select(Account).where(Account.workspace_id == user.active_workspace_id).limit(1)
    )
    tx = Transaction(
        workspace_id=user.active_workspace_id,
        kind="expense", amount_minor=100,
        from_account_id=acc.id, category_id=sys_cat.id,
    )
    db_session.add(tx)
    await db_session.commit()
    personal_ws_id = user.active_workspace_id

    # Soft-delete + сместим timestamp.
    user.deleted_at = datetime.now(timezone.utc) - timedelta(days=31)
    await db_session.commit()

    await hard_purge_user(db_session, user)

    # Personal данные удалены.
    assert (await db_session.scalar(select(User).where(User.id == user.id))) is None
    assert (await db_session.scalar(
        select(Workspace).where(Workspace.id == personal_ws_id)
    )) is None
    assert (await db_session.scalar(
        select(Transaction).where(Transaction.id == tx.id)
    )) is None


async def test_hard_purge_shared_workspace_survives(db_session):
    """A создаёт shared с B, soft-deletes, hard-purge. Shared workspace
    остаётся у B; A's actor_user_id в audit/created_by → SET NULL."""
    a = await ensure_user_provisioned(
        db_session, TelegramUser(id=99020, first_name="A")
    )
    b = await ensure_user_provisioned(
        db_session, TelegramUser(id=99021, first_name="B")
    )
    ws = Workspace(name="Shared", kind="shared")
    db_session.add(ws)
    await db_session.flush()
    db_session.add_all([
        WorkspaceMember(workspace_id=ws.id, user_id=a.id, role="owner"),
        WorkspaceMember(workspace_id=ws.id, user_id=b.id, role="member"),
    ])
    # A создаёт audit-row в shared.
    db_session.add(
        AuditLog(
            workspace_id=ws.id, actor_user_id=a.id,
            entity_type="transaction", entity_id=999, action="create",
            snapshot_json={"after": {"id": 999}, "actor_name_snapshot": "A"},
        )
    )
    await db_session.commit()
    shared_ws_id = ws.id
    audit_id = (await db_session.scalar(
        select(AuditLog.id).where(AuditLog.workspace_id == ws.id)
    ))

    # Soft-delete A + сдвиг timestamp + hard-purge.
    a.deleted_at = datetime.now(timezone.utc) - timedelta(days=31)
    await db_session.commit()
    await hard_purge_user(db_session, a)

    # Shared workspace выжил.
    assert (await db_session.scalar(
        select(Workspace).where(Workspace.id == shared_ws_id)
    )) is not None
    # B membership на месте.
    b_member = await db_session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == shared_ws_id,
            WorkspaceMember.user_id == b.id,
        )
    )
    assert b_member is not None
    # Audit в shared — actor_user_id → NULL (FK SET NULL); snapshot хранит имя.
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.id == audit_id)
    )
    assert audit is not None
    assert audit.actor_user_id is None
    assert audit.snapshot_json["actor_name_snapshot"] == "A"
