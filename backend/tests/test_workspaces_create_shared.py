"""POST /api/workspaces (kind='shared' implicit)."""

from httpx import AsyncClient


async def test_post_shared_workspace_happy(
    app_client: AsyncClient, auth_header, provisioned_user
):
    r = await app_client.post(
        "/api/workspaces", headers=auth_header, json={"name": "Семейный"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "shared"
    assert body["name"] == "Семейный"


async def test_post_workspace_extra_field_kind_rejected_422(
    app_client: AsyncClient, auth_header, provisioned_user
):
    r = await app_client.post(
        "/api/workspaces", headers=auth_header,
        json={"name": "X", "kind": "personal"},  # extra='forbid' отбивает
    )
    assert r.status_code == 422


async def test_post_workspace_empty_name_422(
    app_client: AsyncClient, auth_header, provisioned_user
):
    r = await app_client.post(
        "/api/workspaces", headers=auth_header, json={"name": ""},
    )
    assert r.status_code == 422
