"""Tests for /api/reports/{month,calendar}."""

import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def setup(app_client, provisioned_user, auth_header):
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    card_id = next(a["id"] for a in accounts if a["name"] == "Карта")
    cats = (await app_client.get("/api/categories", headers=auth_header)).json()
    products = next(c["id"] for c in cats if c["name"] == "Продукты" and c["user_id"] is None)
    transport = next(c["id"] for c in cats if c["name"] == "Транспорт" and c["user_id"] is None)
    zarplata = next(c["id"] for c in cats if c["name"] == "Зарплата" and c["user_id"] is None)
    return {
        "card": card_id,
        "products": products,
        "transport": transport,
        "zarplata": zarplata,
    }


async def test_month_empty_returns_zero_shape(
    app_client: AsyncClient, auth_header, setup
):
    """Empty month: by_category всегда {} (dict)."""
    r = await app_client.get("/api/reports/month", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "by_category": {},
        "by_kind": {},
        "total_expense": 0,
        "total_income": 0,
    }


async def test_month_sums_by_category_and_kind(
    app_client: AsyncClient, auth_header, setup
):
    """Создаём 3 транзакции, проверяем агрегаты."""
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
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 1500,
            "from_account_id": setup["card"],
            "category_id": setup["products"],
        },
    )
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 100000,
            "to_account_id": setup["card"],
            "category_id": setup["zarplata"],
        },
    )

    r = await app_client.get("/api/reports/month", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["by_category"] == {"Продукты": 4500}
    assert body["by_kind"] == {"expense": 4500, "income": 100000}
    assert body["total_expense"] == 4500
    assert body["total_income"] == 100000


async def test_month_explicit_year_month_filter(
    app_client: AsyncClient, auth_header, setup
):
    """Указанный year+month → фильтрует на этот месяц."""
    # Транзакция в апреле (через explicit occurred_at).
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 999,
            "from_account_id": setup["card"],
            "category_id": setup["products"],
            "occurred_at": "2026-04-15T12:00:00Z",
        },
    )
    # Транзакция в мае.
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 444,
            "from_account_id": setup["card"],
            "category_id": setup["products"],
            "occurred_at": "2026-05-15T12:00:00Z",
        },
    )

    r_apr = await app_client.get(
        "/api/reports/month?year=2026&month=4", headers=auth_header
    )
    assert r_apr.json()["total_expense"] == 999

    r_may = await app_client.get(
        "/api/reports/month?year=2026&month=5", headers=auth_header
    )
    assert r_may.json()["total_expense"] == 444


async def test_month_invalid_month_returns_422(
    app_client: AsyncClient, auth_header, setup
):
    r = await app_client.get("/api/reports/month?month=13", headers=auth_header)
    assert r.status_code == 422


async def test_calendar_day_aggregation(
    app_client: AsyncClient, auth_header, setup
):
    """3 транзакции, две в один день — одна строка с двумя сумм."""
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 1000,
            "from_account_id": setup["card"],
            "category_id": setup["products"],
            "occurred_at": "2026-05-10T09:00:00Z",
        },
    )
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 500,
            "to_account_id": setup["card"],
            "category_id": setup["zarplata"],
            "occurred_at": "2026-05-10T15:00:00Z",
        },
    )
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 200,
            "from_account_id": setup["card"],
            "category_id": setup["transport"],
            "occurred_at": "2026-05-11T09:00:00Z",
        },
    )

    r = await app_client.get(
        "/api/reports/calendar?from=2026-05-01&to=2026-06-01", headers=auth_header
    )
    assert r.status_code == 200, r.text
    days = r.json()
    assert len(days) == 2
    may_10 = next(d for d in days if d["date"] == "2026-05-10")
    assert may_10 == {"date": "2026-05-10", "expense": 1000, "income": 500}
    may_11 = next(d for d in days if d["date"] == "2026-05-11")
    assert may_11 == {"date": "2026-05-11", "expense": 200, "income": 0}


async def test_calendar_excludes_transfer_and_adjustment(
    app_client: AsyncClient, auth_header, setup
):
    """В calendar только expense + income; transfer и adjustment не входят."""
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    cash_id = next(a["id"] for a in accounts if a["name"] == "Наличные")
    # transfer — между счетами
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "transfer",
            "amount_minor": 5000,
            "from_account_id": setup["card"],
            "to_account_id": cash_id,
            "occurred_at": "2026-05-15T12:00:00Z",
        },
    )

    r = await app_client.get(
        "/api/reports/calendar?from=2026-05-01&to=2026-06-01", headers=auth_header
    )
    assert r.json() == []


async def test_reports_require_auth(app_client: AsyncClient):
    r1 = await app_client.get("/api/reports/month")
    assert r1.status_code == 401
    r2 = await app_client.get("/api/reports/calendar")
    assert r2.status_code == 401
