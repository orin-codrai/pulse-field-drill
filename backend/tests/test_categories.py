"""Tests for /api/categories router.

Покрываем:
- GET: системные (18) + юзеровские; фильтр ?kind=.
- POST: happy, duplicate name 409, extra fields 422.
- PATCH юзеровской категории.
- PATCH/DELETE системной (user_id IS NULL) → 403, не 404.
  Существование системных публично — все юзеры их видят в GET.
- Cross-user user A's category → 404.
"""

from httpx import AsyncClient


async def test_list_includes_system_categories(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.get("/api/categories", headers=auth_header)
    assert r.status_code == 200
    cats = r.json()
    # 18 системных из seed (см. migration 0002).
    system = [c for c in cats if c["user_id"] is None]
    assert len(system) == 18
    # Все имена уникальны.
    names = [c["name"] for c in system]
    assert len(set(names)) == 18


async def test_list_filter_by_kind(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.get("/api/categories?kind=income", headers=auth_header)
    assert r.status_code == 200
    cats = r.json()
    # В системных 1 income (Зарплата).
    assert {c["kind"] for c in cats} == {"income"}
    assert any(c["name"] == "Зарплата" for c in cats)


async def test_post_creates_user_category(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "Хобби", "kind": "expense", "icon": "🎨"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Хобби"
    assert body["user_id"] == provisioned_user.id


async def test_post_duplicate_name_in_user_namespace_returns_409(
    app_client: AsyncClient, provisioned_user, auth_header
):
    await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "Хобби", "kind": "expense"},
    )
    r = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "Хобби", "kind": "income"},
    )
    assert r.status_code == 409


async def test_post_with_same_name_as_system_is_allowed(
    app_client: AsyncClient, provisioned_user, auth_header
):
    """COALESCE(user_id, 0) отделяет namespaces: системное → 0, юзер → user.id.
    Юзер вправе создать свою «Зарплату» одновременно с системной."""
    r = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "Зарплата", "kind": "income"},
    )
    assert r.status_code == 201, r.text


async def test_post_extra_field_rejected_422(
    app_client: AsyncClient, provisioned_user, auth_header
):
    """Mass-assignment regression: схема CategoryCreate с extra='forbid'.
    user_id/is_system/archived_at не должны приниматься в POST body —
    юзер не должен мочь хитростью создать категорию для другого user_id
    или объявить себя системной."""
    r = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "X", "kind": "expense", "user_id": 999},
    )
    assert r.status_code == 422


async def test_patch_extra_field_rejected_422(
    app_client: AsyncClient, provisioned_user, auth_header
):
    """PATCH whitelist: name/icon/archived_at. kind/user_id не принимаются —
    нельзя перевернуть expense в income или передать категорию другому."""
    new = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "Хобби-2", "kind": "expense"},
    )
    cid = new.json()["id"]
    r = await app_client.patch(
        f"/api/categories/{cid}",
        headers=auth_header,
        json={"kind": "income"},
    )
    assert r.status_code == 422


async def test_post_invalid_kind_422(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "X", "kind": "stonk"},
    )
    assert r.status_code == 422


async def test_patch_user_category_works(
    app_client: AsyncClient, provisioned_user, auth_header
):
    new = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "Хобби", "kind": "expense"},
    )
    cid = new.json()["id"]
    r = await app_client.patch(
        f"/api/categories/{cid}", headers=auth_header, json={"icon": "🎲"}
    )
    assert r.status_code == 200
    assert r.json()["icon"] == "🎲"


async def test_patch_system_category_returns_403(
    app_client: AsyncClient, provisioned_user, auth_header
):
    cats = (await app_client.get("/api/categories", headers=auth_header)).json()
    system_cat = next(c for c in cats if c["user_id"] is None)
    r = await app_client.patch(
        f"/api/categories/{system_cat['id']}",
        headers=auth_header,
        json={"name": "Hijacked"},
    )
    assert r.status_code == 403


async def test_patch_other_users_category_returns_404(
    app_client: AsyncClient, provisioned_user, auth_header, db_session
):
    """Создать категорию у user B, попытаться спрятать её под user A → 404
    (не 403 — существование чужих ID не должно палить)."""
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=88888, first_name="Bob")
    )
    await db_session.commit()

    from app.models import Category

    bob_cat = Category(user_id=user_b.id, name="Bob's stuff", kind="expense")
    db_session.add(bob_cat)
    await db_session.commit()

    r = await app_client.patch(
        f"/api/categories/{bob_cat.id}",
        headers=auth_header,
        json={"name": "Hijacked"},
    )
    assert r.status_code == 404
