"""Tests for /api/accounts router.

Покрываем:
- 401 без auth.
- GET: возвращает дефолтные accounts провизионенного юзера.
- POST happy.
- POST с дубликатом name → 409.
- POST с extra полем (currency) → 422 (mass-assignment whitelist).
- PATCH happy: name/icon.
- PATCH archive/un-archive.
- Cross-resource: user B → GET/PATCH ресурса user A → 404.
"""

import pytest
from httpx import AsyncClient

from tests.conftest import sign_init_data


async def test_unauthorized_without_header(app_client: AsyncClient):
    r = await app_client.get("/api/accounts")
    assert r.status_code == 401


async def test_list_returns_default_accounts(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.get("/api/accounts", headers=auth_header)
    assert r.status_code == 200
    accounts = r.json()
    names = [a["name"] for a in accounts]
    assert names == ["Карта", "Наличные"]
    assert all(a["currency"] == "RUB" for a in accounts)
    assert all(a["initial_balance_minor"] == 0 for a in accounts)


async def test_post_creates_account(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.post(
        "/api/accounts",
        headers=auth_header,
        json={"name": "Сбережения", "type": "savings", "initial_balance_minor": 50000},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Сбережения"
    assert body["type"] == "savings"
    assert body["currency"] == "RUB"
    assert body["initial_balance_minor"] == 50000


async def test_post_duplicate_name_returns_409(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.post(
        "/api/accounts",
        headers=auth_header,
        json={"name": "Карта", "type": "card"},
    )
    assert r.status_code == 409


async def test_post_with_currency_is_rejected_422(
    app_client: AsyncClient, provisioned_user, auth_header
):
    """POST схема не принимает currency — сервер пинит 'RUB'. Иначе
    клиент мог бы прислать 'USD', БД ругалась бы CHECK-ом и юзер бы
    видел 500. Pydantic extra='forbid' → 422."""
    r = await app_client.post(
        "/api/accounts",
        headers=auth_header,
        json={"name": "Доллары", "type": "card", "currency": "USD"},
    )
    assert r.status_code == 422


async def test_post_invalid_type_returns_422(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.post(
        "/api/accounts",
        headers=auth_header,
        json={"name": "X", "type": "stonk"},
    )
    assert r.status_code == 422


async def test_patch_updates_name_and_icon(
    app_client: AsyncClient, provisioned_user, auth_header, db_session
):
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    aid = accounts[0]["id"]
    r = await app_client.patch(
        f"/api/accounts/{aid}",
        headers=auth_header,
        json={"name": "Карта Сбер", "icon": "🟢"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Карта Сбер"
    assert r.json()["icon"] == "🟢"


async def test_patch_archive_then_unarchive(
    app_client: AsyncClient, provisioned_user, auth_header
):
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    aid = accounts[0]["id"]
    r1 = await app_client.patch(
        f"/api/accounts/{aid}",
        headers=auth_header,
        json={"archived_at": "2026-05-18T12:00:00Z"},
    )
    assert r1.status_code == 200
    assert r1.json()["archived_at"] is not None

    r2 = await app_client.patch(
        f"/api/accounts/{aid}",
        headers=auth_header,
        json={"archived_at": None},
    )
    assert r2.status_code == 200
    assert r2.json()["archived_at"] is None


async def test_cross_user_404(
    app_client: AsyncClient, provisioned_user, auth_header, db_session
):
    """User B пытается GET/PATCH ресурс user A → 404, не 403.
    Существование чужих ID не должно утекать."""
    # Создать второго юзера + его accounts через прямой provision
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b_tg = TelegramUser(id=99999, first_name="Bob")
    user_b = await ensure_user_provisioned(db_session, user_b_tg)
    await db_session.commit()

    # Найти один из его accounts
    from sqlalchemy import select

    from app.models import Account

    b_account_id = await db_session.scalar(
        select(Account.id).where(Account.user_id == user_b.id).limit(1)
    )
    assert b_account_id is not None

    # User A (через auth_header, tg_id=12345) пытается достучаться к B's account.
    r = await app_client.patch(
        f"/api/accounts/{b_account_id}",
        headers=auth_header,
        json={"name": "Hijacked"},
    )
    assert r.status_code == 404


async def test_invalid_init_data_returns_401(app_client: AsyncClient):
    """Подделанный hash → 401 (через tg_user_from_auth)."""
    bad = sign_init_data({"id": 1, "first_name": "X"}, bot_token="wrong:token")
    r = await app_client.get("/api/accounts", headers={"Authorization": f"tma {bad}"})
    assert r.status_code == 401
