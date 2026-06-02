"""Unit-тесты services/invites.py. Покрывают create_invite + accept_invite
happy-path. Concurrent race тесты — в test_invites.py (через HTTP).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Workspace, WorkspaceInvite, WorkspaceMember
from app.schemas.user import TelegramUser
from app.services.invites import (
    INVITE_TTL_DAYS,
    InviteError,
    accept_invite,
    create_invite,
)
from app.services.user_provisioning import ensure_user_provisioned


async def _create_user(db_session, tg_id: int, name: str):
    user = await ensure_user_provisioned(
        db_session, TelegramUser(id=tg_id, first_name=name)
    )
    await db_session.commit()
    return user


async def _make_shared(db_session, owner) -> Workspace:
    ws = Workspace(name="Семейный", kind="shared")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws.id, user_id=owner.id, role="owner")
    )
    await db_session.commit()
    return ws


async def test_create_invite_happy(db_session):
    owner = await _create_user(db_session, 81001, "A")
    ws = await _make_shared(db_session, owner)

    invite = await create_invite(db_session, workspace=ws, actor=owner)
    await db_session.commit()

    assert invite.workspace_id == ws.id
    assert invite.token  # non-empty
    assert len(invite.token) > 30
    assert invite.status == "pending"
    assert invite.created_by_user_id == owner.id
    delta_days = (invite.expires_at - datetime.now(timezone.utc)).days
    assert INVITE_TTL_DAYS - 1 <= delta_days <= INVITE_TTL_DAYS


async def test_create_invite_on_personal_raises(db_session):
    user = await _create_user(db_session, 81002, "Solo")
    personal = await db_session.get(Workspace, user.active_workspace_id)

    with pytest.raises(ValueError, match="non-shared"):
        await create_invite(db_session, workspace=personal, actor=user)


async def test_accept_invite_happy(db_session):
    owner = await _create_user(db_session, 81010, "A")
    ws = await _make_shared(db_session, owner)
    invite = await create_invite(db_session, workspace=ws, actor=owner)
    await db_session.commit()

    bob = await _create_user(db_session, 81011, "B")
    workspace = await accept_invite(
        db_session, token=invite.token, accepting_user=bob
    )
    await db_session.commit()

    assert workspace.id == ws.id
    # Membership создан.
    member = await db_session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.user_id == bob.id,
        )
    )
    assert member is not None
    assert member.role == "member"
    # Invite terminal accepted.
    refreshed = await db_session.scalar(
        select(WorkspaceInvite).where(WorkspaceInvite.id == invite.id)
    )
    assert refreshed.status == "accepted"
    assert refreshed.accepted_by_user_id == bob.id
    assert refreshed.accepted_at is not None


async def test_accept_invite_not_found(db_session):
    user = await _create_user(db_session, 81020, "Lone")
    with pytest.raises(InviteError) as exc:
        await accept_invite(
            db_session, token="nonexistent-token", accepting_user=user
        )
    assert exc.value.code == "not_found"
    assert exc.value.http_status == 404


async def test_accept_invite_already_member(db_session):
    owner = await _create_user(db_session, 81030, "A")
    ws = await _make_shared(db_session, owner)
    invite = await create_invite(db_session, workspace=ws, actor=owner)
    await db_session.commit()

    with pytest.raises(InviteError) as exc:
        await accept_invite(
            db_session, token=invite.token, accepting_user=owner
        )
    assert exc.value.code == "already_member"
    assert exc.value.http_status == 409


async def test_accept_invite_cap_workspace_reached(db_session):
    """Cap-2 членов: третий accept → cap_reached."""
    owner = await _create_user(db_session, 81040, "A")
    ws = await _make_shared(db_session, owner)
    inv1 = await create_invite(db_session, workspace=ws, actor=owner)
    await db_session.commit()

    bob = await _create_user(db_session, 81041, "B")
    await accept_invite(db_session, token=inv1.token, accepting_user=bob)
    await db_session.commit()

    # Третий invite → попытка accept третьим юзером.
    inv2 = await create_invite(db_session, workspace=ws, actor=owner)
    await db_session.commit()
    charlie = await _create_user(db_session, 81042, "C")
    with pytest.raises(InviteError) as exc:
        await accept_invite(db_session, token=inv2.token, accepting_user=charlie)
    assert exc.value.code == "cap_reached"


async def test_accept_invite_expired(db_session):
    owner = await _create_user(db_session, 81050, "A")
    ws = await _make_shared(db_session, owner)
    invite = await create_invite(db_session, workspace=ws, actor=owner)
    # Симулируем просроченный invite — двинуть expires_at в прошлое.
    invite.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    bob = await _create_user(db_session, 81051, "B")
    with pytest.raises(InviteError) as exc:
        await accept_invite(db_session, token=invite.token, accepting_user=bob)
    assert exc.value.code == "expired"
    assert exc.value.http_status == 410
    # Lazy-set status='expired'.
    refreshed = await db_session.scalar(
        select(WorkspaceInvite).where(WorkspaceInvite.id == invite.id)
    )
    assert refreshed.status == "expired"


async def test_accept_invite_not_pending_revoked(db_session):
    owner = await _create_user(db_session, 81060, "A")
    ws = await _make_shared(db_session, owner)
    invite = await create_invite(db_session, workspace=ws, actor=owner)
    invite.status = "revoked"
    await db_session.commit()

    bob = await _create_user(db_session, 81061, "B")
    with pytest.raises(InviteError) as exc:
        await accept_invite(db_session, token=invite.token, accepting_user=bob)
    assert exc.value.code == "not_pending"
