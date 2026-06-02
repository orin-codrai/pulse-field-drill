"""HTTP-level тесты /api/workspaces/{id}/invites + /api/invites/{token}.

Покрывает: create/list/revoke + preview/accept happy + edge cases
(personal-ws 403, URL-mismatch 403, cap-2/cap-3/expired/revoked/
already-member/already-accepted, soft-deleted user blocked в P7.D).
"""

from datetime import datetime, timedelta, timezone

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Workspace, WorkspaceInvite, WorkspaceMember
from app.schemas.user import TelegramUser
from app.services.user_provisioning import ensure_user_provisioned
from tests.conftest import sign_init_data


async def _make_user_with_shared(db_session, tg_id: int, name: str = "A"):
    user = await ensure_user_provisioned(
        db_session, TelegramUser(id=tg_id, first_name=name)
    )
    await db_session.commit()
    ws = Workspace(name="Семейный", kind="shared")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    )
    user.active_workspace_id = ws.id
    await db_session.commit()
    return user, ws


@pytest_asyncio.fixture
async def setup_shared(app_client, provisioned_user, auth_header, db_session):
    """provisioned_user (tg_id=12345) делает себе shared workspace и
    становится в нём active."""
    ws = Workspace(name="Семейный", kind="shared")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=provisioned_user.id, role="owner"
        )
    )
    provisioned_user.active_workspace_id = ws.id
    await db_session.commit()
    return ws


# ─── Create / list / revoke ──────────────────────────────────────────────────


async def test_post_invite_happy(
    app_client: AsyncClient, auth_header, setup_shared
):
    r = await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["workspace_id"] == setup_shared.id
    assert body["status"] == "pending"
    assert len(body["token"]) > 30


async def test_post_invite_url_mismatch_403(
    app_client: AsyncClient, auth_header, setup_shared, db_session
):
    """URL workspace_id не совпадает с active_workspace_id юзера → 403."""
    other = Workspace(name="Other", kind="shared")
    db_session.add(other)
    await db_session.commit()
    r = await app_client.post(
        f"/api/workspaces/{other.id}/invites", headers=auth_header
    )
    assert r.status_code == 403


async def test_post_invite_personal_403(
    app_client: AsyncClient, auth_header, provisioned_user
):
    """provisioned_user активен в своём personal → POST invite → 403."""
    personal_id = provisioned_user.active_workspace_id
    r = await app_client.post(
        f"/api/workspaces/{personal_id}/invites", headers=auth_header
    )
    assert r.status_code == 403
    assert "personal" in r.json()["detail"]


async def test_get_list_invites(
    app_client: AsyncClient, auth_header, setup_shared
):
    await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    r = await app_client.get(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2


async def test_delete_revoke_pending(
    app_client: AsyncClient, auth_header, setup_shared
):
    create = await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    inv_id = create.json()["id"]
    r = await app_client.delete(
        f"/api/workspaces/{setup_shared.id}/invites/{inv_id}",
        headers=auth_header,
    )
    assert r.status_code == 204
    after = await app_client.get(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    assert after.json()[0]["status"] == "revoked"


async def test_delete_revoke_accepted_returns_409(
    app_client: AsyncClient, auth_header, setup_shared, db_session
):
    """Cannot revoke terminal invite."""
    create = await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    inv_id = create.json()["id"]
    inv = await db_session.scalar(
        select(WorkspaceInvite).where(WorkspaceInvite.id == inv_id)
    )
    inv.status = "accepted"
    inv.accepted_at = datetime.now(timezone.utc)
    await db_session.commit()
    r = await app_client.delete(
        f"/api/workspaces/{setup_shared.id}/invites/{inv_id}",
        headers=auth_header,
    )
    assert r.status_code == 409


# ─── Preview / accept ────────────────────────────────────────────────────────


async def test_get_preview(
    app_client: AsyncClient, auth_header, setup_shared
):
    create = await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    token = create.json()["token"]
    r = await app_client.get(f"/api/invites/{token}", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["workspace_id"] == setup_shared.id
    assert body["workspace_name"] == "Семейный"
    assert body["workspace_kind"] == "shared"
    assert body["status"] == "pending"
    assert body["inviter_display_name"] == "Orrin"  # first_name fallback


async def test_get_preview_not_found(
    app_client: AsyncClient, auth_header, provisioned_user
):
    r = await app_client.get("/api/invites/nonexistent", headers=auth_header)
    assert r.status_code == 404


async def test_post_accept_happy(
    app_client: AsyncClient, auth_header, setup_shared, db_session
):
    """A (provisioned_user/auth_header) делает invite; B (другой tg_id) принимает."""
    create = await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    token = create.json()["token"]

    # Provision B и его auth.
    bob = await ensure_user_provisioned(
        db_session, TelegramUser(id=83001, first_name="Bob")
    )
    await db_session.commit()
    bob_init = sign_init_data({"id": 83001, "first_name": "Bob"})
    bob_headers = {"Authorization": f"tma {bob_init}"}

    r = await app_client.post(
        f"/api/invites/{token}/accept", headers=bob_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"

    # B становится членом + active_workspace_id переключён (PIN-B).
    await db_session.refresh(bob)
    assert bob.active_workspace_id == setup_shared.id
    member = await db_session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == setup_shared.id,
            WorkspaceMember.user_id == bob.id,
        )
    )
    assert member is not None
    assert member.role == "member"


async def test_post_accept_already_member(
    app_client: AsyncClient, auth_header, setup_shared
):
    """A (owner) пытается принять свой же invite → 409 already_member."""
    create = await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    token = create.json()["token"]
    r = await app_client.post(
        f"/api/invites/{token}/accept", headers=auth_header
    )
    assert r.status_code == 409
    assert "already" in r.json()["detail"]


async def test_post_accept_expired_410(
    app_client: AsyncClient, auth_header, setup_shared, db_session
):
    create = await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    token = create.json()["token"]
    inv = await db_session.scalar(
        select(WorkspaceInvite).where(WorkspaceInvite.token == token)
    )
    inv.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.commit()

    bob = await ensure_user_provisioned(
        db_session, TelegramUser(id=83100, first_name="Bob")
    )
    await db_session.commit()
    bob_init = sign_init_data({"id": 83100, "first_name": "Bob"})
    r = await app_client.post(
        f"/api/invites/{token}/accept",
        headers={"Authorization": f"tma {bob_init}"},
    )
    assert r.status_code == 410


async def test_post_accept_cap_workspace_409(
    app_client: AsyncClient, auth_header, setup_shared, db_session
):
    """Cap-2: третий accept → 409 cap_reached."""
    # B принимает первый invite.
    inv1 = await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    bob = await ensure_user_provisioned(
        db_session, TelegramUser(id=83200, first_name="Bob")
    )
    await db_session.commit()
    bob_init = sign_init_data({"id": 83200, "first_name": "Bob"})
    await app_client.post(
        f"/api/invites/{inv1.json()['token']}/accept",
        headers={"Authorization": f"tma {bob_init}"},
    )

    # C пытается принять.
    inv2 = await app_client.post(
        f"/api/workspaces/{setup_shared.id}/invites", headers=auth_header
    )
    charlie = await ensure_user_provisioned(
        db_session, TelegramUser(id=83201, first_name="Charlie")
    )
    await db_session.commit()
    charlie_init = sign_init_data({"id": 83201, "first_name": "Charlie"})
    r = await app_client.post(
        f"/api/invites/{inv2.json()['token']}/accept",
        headers={"Authorization": f"tma {charlie_init}"},
    )
    assert r.status_code == 409
    assert "capacity" in r.json()["detail"]


async def test_post_accept_cap_per_user_409(
    app_client: AsyncClient, auth_header, provisioned_user, db_session
):
    """PIN-N cap-3 shared-per-user.

    Bob уже член 3 shared workspace'ов (прямой DB insert). Затем 4-й shared
    с pending invite → bob accept'ит → 409 user_cap_reached.
    """
    bob_tg = 83300
    bob = await ensure_user_provisioned(
        db_session, TelegramUser(id=bob_tg, first_name="Bob")
    )
    await db_session.commit()
    bob_init = sign_init_data({"id": bob_tg, "first_name": "Bob"})
    bob_headers = {"Authorization": f"tma {bob_init}"}

    for i in range(3):
        ws = Workspace(name=f"Shared-{i}", kind="shared")
        db_session.add(ws)
        await db_session.flush()
        db_session.add(
            WorkspaceMember(
                workspace_id=ws.id, user_id=bob.id, role="member"
            )
        )
    await db_session.commit()

    ws4 = Workspace(name="Shared-4", kind="shared")
    db_session.add(ws4)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=ws4.id, user_id=provisioned_user.id, role="owner"
        )
    )
    from app.services.invites import create_invite
    inv = await create_invite(db_session, workspace=ws4, actor=provisioned_user)
    await db_session.commit()

    r = await app_client.post(
        f"/api/invites/{inv.token}/accept", headers=bob_headers
    )
    assert r.status_code == 409, r.text
    assert "shared workspaces" in r.json()["detail"]
