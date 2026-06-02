"""POST /me/delete + /me/restore + dependency-410."""

from datetime import datetime, timedelta, timezone

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Workspace, WorkspaceMember
from app.schemas.user import TelegramUser
from app.services.user_provisioning import ensure_user_provisioned


async def test_post_delete_sets_state(
    app_client: AsyncClient, auth_header, provisioned_user, db_session
):
    r = await app_client.post("/api/me/delete", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["deleted_at"] is not None
    assert body["active_workspace_id"] is None

    # Personal workspace архивирован.
    await db_session.refresh(provisioned_user)
    personal = await db_session.scalar(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == provisioned_user.id,
            Workspace.kind == "personal",
        )
    )
    assert personal is not None
    assert personal.archived_at is not None


async def test_post_delete_removes_shared_membership(
    app_client: AsyncClient, auth_header, provisioned_user, db_session
):
    """PIN-G: shared workspace выживает, но membership юзера убирается."""
    ws = Workspace(name="Shared", kind="shared")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=provisioned_user.id, role="owner"
        )
    )
    await db_session.commit()

    await app_client.post("/api/me/delete", headers=auth_header)

    # Shared workspace exists.
    ws_after = await db_session.get(Workspace, ws.id)
    assert ws_after is not None
    assert ws_after.archived_at is None  # shared НЕ архивируется
    # Membership убрана.
    m = await db_session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.user_id == provisioned_user.id,
        )
    )
    assert m is None


async def test_post_delete_idempotent(
    app_client: AsyncClient, auth_header, provisioned_user
):
    await app_client.post("/api/me/delete", headers=auth_header)
    r2 = await app_client.post("/api/me/delete", headers=auth_header)
    assert r2.status_code == 200  # no-op, не raise


async def test_post_restore_happy(
    app_client: AsyncClient, auth_header, provisioned_user, db_session
):
    """Soft-delete + restore в окне."""
    original_ws = provisioned_user.active_workspace_id
    await app_client.post("/api/me/delete", headers=auth_header)
    r = await app_client.post("/api/me/restore", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["deleted_at"] is None
    assert body["active_workspace_id"] == original_ws

    ws = await db_session.get(Workspace, original_ws)
    assert ws.archived_at is None


async def test_post_restore_past_window_410(
    app_client: AsyncClient, auth_header, provisioned_user, db_session
):
    """deleted_at > 30 дней назад → 410."""
    provisioned_user.deleted_at = datetime.now(timezone.utc) - timedelta(days=31)
    provisioned_user.active_workspace_id = None
    await db_session.commit()

    r = await app_client.post("/api/me/restore", headers=auth_header)
    assert r.status_code == 410


async def test_post_restore_no_op_if_not_deleted(
    app_client: AsyncClient, auth_header, provisioned_user
):
    r = await app_client.post("/api/me/restore", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["deleted_at"] is None


async def test_post_restore_does_not_recover_shared_membership(
    app_client: AsyncClient, auth_header, provisioned_user, db_session
):
    """PIN-G: restore возвращает personal, но НЕ восстанавливает shared
    membership. Для shared B должен пригласить заново."""
    ws = Workspace(name="Shared", kind="shared")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=provisioned_user.id, role="owner"
        )
    )
    await db_session.commit()

    await app_client.post("/api/me/delete", headers=auth_header)
    await app_client.post("/api/me/restore", headers=auth_header)

    m = await db_session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.user_id == provisioned_user.id,
        )
    )
    assert m is None  # shared membership НЕ вернулся (PIN-G)


# ─── Dependency 410 ─────────────────────────────────────────────────────────


async def test_active_user_blocks_soft_deleted_on_resource_endpoints(
    app_client: AsyncClient, auth_header, provisioned_user, db_session
):
    """После /me/delete любой ресурсный endpoint → 410."""
    await app_client.post("/api/me/delete", headers=auth_header)

    # /me/restore разрешён (использует current_user, не active_user).
    r_restore = await app_client.post("/api/me/restore", headers=auth_header)
    assert r_restore.status_code == 200

    # Снова удалим — теперь проверим ресурсные эндпоинты.
    await app_client.post("/api/me/delete", headers=auth_header)

    r_acc = await app_client.get("/api/accounts", headers=auth_header)
    assert r_acc.status_code == 410


async def test_registered_user_blocks_unregistered_on_sharing(
    app_client: AsyncClient, auth_header, provisioned_user
):
    """Без display_name/consent → POST /workspaces → 412."""
    r = await app_client.post(
        "/api/workspaces", headers=auth_header, json={"name": "Shared"},
    )
    assert r.status_code == 412
    assert "registration" in r.json()["detail"]
