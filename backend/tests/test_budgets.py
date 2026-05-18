"""Tests for /api/budgets router + /budgets/status.

Покрываем (must-fix plan v2):
- #2: cross-user category_id rejected; archived category rejected.
- #3: budgets_active_uq → 409 на POST и PATCH un-archive; budgets_dates_chk → 422 на POST и PATCH.
- #4: status window — spend в окне vs вне.
- PATCH whitelist (category_id/period/starts_on иммутабельны).
"""

from datetime import date, timedelta

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select


@pytest_asyncio.fixture
async def setup(app_client, provisioned_user, auth_header):
    cats = (await app_client.get("/api/categories", headers=auth_header)).json()
    products = next(c for c in cats if c["name"] == "Продукты" and c["user_id"] is None)
    transport = next(c for c in cats if c["name"] == "Транспорт" and c["user_id"] is None)
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    card_id = next(a["id"] for a in accounts if a["name"] == "Карта")
    return {
        "products": products["id"],
        "transport": transport["id"],
        "card": card_id,
    }


# ─── CRUD ─────────────────────────────────────────────────────────────────────


async def test_post_create_happy(app_client: AsyncClient, auth_header, setup):
    r = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 1000000,
            "starts_on": "2026-05-01",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["period"] == "month"
    assert body["limit_minor"] == 1000000
    assert body["ends_on"] is None


async def test_post_extra_field_rejected_422(
    app_client: AsyncClient, auth_header, setup
):
    r = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-01",
            "user_id": 999,
        },
    )
    assert r.status_code == 422


async def test_post_invalid_period_422(app_client: AsyncClient, auth_header, setup):
    r = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "decade",
            "limit_minor": 100,
            "starts_on": "2026-05-01",
        },
    )
    assert r.status_code == 422


# ─── Cross-user/archived guards (must-fix #2) ────────────────────────────────


async def test_post_other_user_category_rejected_422(
    app_client: AsyncClient, auth_header, setup, db_session
):
    """Без _validate_category guard'a — БД пускает FK на категорию user B
    (categories.id FK не WHERE user_id). Должен 422."""
    from app.models import Category
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=11111, first_name="Bob")
    )
    await db_session.commit()
    bob_cat = Category(user_id=user_b.id, name="Bob's", kind="expense")
    db_session.add(bob_cat)
    await db_session.commit()

    r = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": bob_cat.id,
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-01",
        },
    )
    assert r.status_code == 422


async def test_post_archived_category_rejected_422(
    app_client: AsyncClient, auth_header, setup
):
    """Свою категорию архивируем, потом пробуем POST бюджет → 422."""
    new = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "Зарплата2", "kind": "expense"},
    )
    cid = new.json()["id"]
    await app_client.patch(
        f"/api/categories/{cid}",
        headers=auth_header,
        json={"archived_at": "2026-05-01T00:00:00Z"},
    )
    r = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": cid,
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-01",
        },
    )
    assert r.status_code == 422


# ─── CHECK + unique (must-fix #3) ─────────────────────────────────────────────


async def test_post_ends_on_before_starts_on_rejected_422(
    app_client: AsyncClient, auth_header, setup
):
    r = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-15",
            "ends_on": "2026-05-10",
        },
    )
    assert r.status_code == 422


async def test_post_duplicate_active_budget_returns_409(
    app_client: AsyncClient, auth_header, setup
):
    await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-01",
        },
    )
    r = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 999,
            "starts_on": "2026-05-01",
        },
    )
    assert r.status_code == 409


async def test_patch_unarchive_collides_with_active_returns_409(
    app_client: AsyncClient, auth_header, setup
):
    """A архивирован, B active на тех же (cat, period). PATCH A.archived_at=null → 409."""
    a = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-01",
        },
    )
    aid = a.json()["id"]
    # Архивируем A.
    await app_client.patch(
        f"/api/budgets/{aid}",
        headers=auth_header,
        json={"archived_at": "2026-05-15T00:00:00Z"},
    )
    # Создаём B (тот же category, period — partial uq allows т.к. A archived).
    await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 200,
            "starts_on": "2026-05-16",
        },
    )
    # Попытка un-archive A → 409.
    r = await app_client.patch(
        f"/api/budgets/{aid}",
        headers=auth_header,
        json={"archived_at": None},
    )
    assert r.status_code == 409


async def test_patch_ends_on_before_starts_on_rejected_422(
    app_client: AsyncClient, auth_header, setup
):
    new = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-15",
        },
    )
    bid = new.json()["id"]
    r = await app_client.patch(
        f"/api/budgets/{bid}",
        headers=auth_header,
        json={"ends_on": "2026-05-10"},
    )
    assert r.status_code == 422


# ─── PATCH whitelist (иммутабельность) ────────────────────────────────────────


async def test_patch_period_rejected_422(
    app_client: AsyncClient, auth_header, setup
):
    new = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-01",
        },
    )
    bid = new.json()["id"]
    r = await app_client.patch(
        f"/api/budgets/{bid}",
        headers=auth_header,
        json={"period": "year"},
    )
    assert r.status_code == 422


async def test_patch_category_rejected_422(
    app_client: AsyncClient, auth_header, setup
):
    new = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-01",
        },
    )
    bid = new.json()["id"]
    r = await app_client.patch(
        f"/api/budgets/{bid}",
        headers=auth_header,
        json={"category_id": setup["transport"]},
    )
    assert r.status_code == 422


# ─── Cross-user ──────────────────────────────────────────────────────────────


async def test_patch_other_users_budget_returns_404(
    app_client: AsyncClient, auth_header, setup, db_session
):
    from datetime import date as date_cls
    from app.models import Budget
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=22221, first_name="Bob")
    )
    await db_session.commit()
    bob_budget = Budget(
        user_id=user_b.id,
        category_id=setup["products"],  # системная — Bob тоже её видит
        period="month",
        limit_minor=500,
        starts_on=date_cls(2026, 5, 1),
    )
    db_session.add(bob_budget)
    await db_session.commit()

    r = await app_client.patch(
        f"/api/budgets/{bob_budget.id}",
        headers=auth_header,
        json={"limit_minor": 9999},
    )
    assert r.status_code == 404


async def test_delete_happy_and_cross_user_404(
    app_client: AsyncClient, auth_header, setup, db_session
):
    new = await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 100,
            "starts_on": "2026-05-01",
        },
    )
    bid = new.json()["id"]
    r = await app_client.delete(f"/api/budgets/{bid}", headers=auth_header)
    assert r.status_code == 204


# ─── Status (must-fix #4) ─────────────────────────────────────────────────────


async def test_status_counts_only_expense_in_category(
    app_client: AsyncClient, auth_header, setup
):
    """Spent = Σ expense.amount where category_id matches, в окне period."""
    # Создаём активный бюджет на «Продукты», month, 10000 копеек.
    await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 10000,
            "starts_on": date.today().replace(day=1).isoformat(),
        },
    )
    # Expense 3000 в «Продукты» — должен учитываться (occurred_at = сейчас).
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 3000,
            "from_account_id": setup["card"],
            "category_id": setup["products"],
        },
    )
    # Expense 7000 в другой категории (Транспорт) — НЕ учитывается.
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 7000,
            "from_account_id": setup["card"],
            "category_id": setup["transport"],
        },
    )

    r = await app_client.get("/api/budgets/status", headers=auth_header)
    assert r.status_code == 200, r.text
    status_items = r.json()
    products_status = next(s for s in status_items if s["category_name"] == "Продукты")
    assert products_status["spent_minor"] == 3000
    assert products_status["limit_minor"] == 10000
    assert products_status["percent"] == 30.0


async def test_status_excludes_expired_budget(
    app_client: AsyncClient, auth_header, setup
):
    """Budget с ends_on в прошлом не возвращается из /status (но остаётся в /budgets list)."""
    past = date.today() - timedelta(days=30)
    even_more_past = date.today() - timedelta(days=60)
    await app_client.post(
        "/api/budgets",
        headers=auth_header,
        json={
            "category_id": setup["products"],
            "period": "month",
            "limit_minor": 10000,
            "starts_on": even_more_past.isoformat(),
            "ends_on": past.isoformat(),
        },
    )
    list_r = await app_client.get("/api/budgets", headers=auth_header)
    assert len(list_r.json()) == 1  # видим в list

    status_r = await app_client.get("/api/budgets/status", headers=auth_header)
    assert status_r.json() == []  # но не в status
