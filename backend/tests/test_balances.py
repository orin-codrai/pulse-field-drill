"""Tests for balances service + GET /api/accounts/balances.

Сценарии из плана: expense −, income +, transfer и +/−, adjustment +/−,
DELETE откатывает влияние. Один SQL, проверяем суммы напрямую.
"""

import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def setup(app_client, provisioned_user, auth_header):
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    card_id = next(a["id"] for a in accounts if a["name"] == "Карта")
    cash_id = next(a["id"] for a in accounts if a["name"] == "Наличные")
    cats = (await app_client.get("/api/categories", headers=auth_header)).json()
    expense_cat = next(c["id"] for c in cats if c["kind"] == "expense" and c["user_id"] is None)
    income_cat = next(c["id"] for c in cats if c["kind"] == "income")
    adjust_cat = next(c["id"] for c in cats if c["kind"] == "both")
    return {
        "card": card_id,
        "cash": cash_id,
        "exp": expense_cat,
        "inc": income_cat,
        "adj": adjust_cat,
    }


async def balance_of(app_client, auth_header, account_id):
    r = await app_client.get("/api/accounts/balances", headers=auth_header)
    assert r.status_code == 200, r.text
    for item in r.json():
        if item["account_id"] == account_id:
            return item["balance_minor"]
    raise AssertionError(f"account {account_id} not in balances")


async def post_tx(app_client, auth_header, **kwargs):
    r = await app_client.post(
        "/api/transactions", headers=auth_header, json=kwargs
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_initial_balances_are_zero(app_client: AsyncClient, auth_header, setup):
    assert await balance_of(app_client, auth_header, setup["card"]) == 0
    assert await balance_of(app_client, auth_header, setup["cash"]) == 0


async def test_expense_subtracts(app_client: AsyncClient, auth_header, setup):
    await post_tx(
        app_client,
        auth_header,
        kind="expense",
        amount_minor=3000,
        from_account_id=setup["card"],
        category_id=setup["exp"],
    )
    assert await balance_of(app_client, auth_header, setup["card"]) == -3000
    assert await balance_of(app_client, auth_header, setup["cash"]) == 0


async def test_income_adds(app_client: AsyncClient, auth_header, setup):
    await post_tx(
        app_client,
        auth_header,
        kind="income",
        amount_minor=50000,
        to_account_id=setup["card"],
        category_id=setup["inc"],
    )
    assert await balance_of(app_client, auth_header, setup["card"]) == 50000


async def test_transfer_moves_money(app_client: AsyncClient, auth_header, setup):
    await post_tx(
        app_client,
        auth_header,
        kind="income",
        amount_minor=10000,
        to_account_id=setup["card"],
        category_id=setup["inc"],
    )
    await post_tx(
        app_client,
        auth_header,
        kind="transfer",
        amount_minor=2000,
        from_account_id=setup["card"],
        to_account_id=setup["cash"],
    )
    assert await balance_of(app_client, auth_header, setup["card"]) == 8000
    assert await balance_of(app_client, auth_header, setup["cash"]) == 2000


async def test_adjustment_in_adds(app_client: AsyncClient, auth_header, setup):
    await post_tx(
        app_client,
        auth_header,
        kind="adjustment",
        amount_minor=500,
        to_account_id=setup["card"],
        category_id=setup["adj"],
    )
    assert await balance_of(app_client, auth_header, setup["card"]) == 500


async def test_adjustment_out_subtracts(
    app_client: AsyncClient, auth_header, setup
):
    await post_tx(
        app_client,
        auth_header,
        kind="adjustment",
        amount_minor=300,
        from_account_id=setup["card"],
        category_id=setup["adj"],
    )
    assert await balance_of(app_client, auth_header, setup["card"]) == -300


async def test_full_scenario_from_plan(app_client: AsyncClient, auth_header, setup):
    """План: initial=0 → expense 3000 (−3000) → income 5000 (+2000) →
    transfer 1000 to cash (card: 1000, cash: 1000) → adjustment +500 on card
    (card: 1500, cash: 1000)."""
    await post_tx(
        app_client,
        auth_header,
        kind="expense",
        amount_minor=3000,
        from_account_id=setup["card"],
        category_id=setup["exp"],
    )
    await post_tx(
        app_client,
        auth_header,
        kind="income",
        amount_minor=5000,
        to_account_id=setup["card"],
        category_id=setup["inc"],
    )
    await post_tx(
        app_client,
        auth_header,
        kind="transfer",
        amount_minor=1000,
        from_account_id=setup["card"],
        to_account_id=setup["cash"],
    )
    await post_tx(
        app_client,
        auth_header,
        kind="adjustment",
        amount_minor=500,
        to_account_id=setup["card"],
        category_id=setup["adj"],
    )
    assert await balance_of(app_client, auth_header, setup["card"]) == 1500
    assert await balance_of(app_client, auth_header, setup["cash"]) == 1000


async def test_delete_reverts_effect(app_client: AsyncClient, auth_header, setup):
    tx_id = await post_tx(
        app_client,
        auth_header,
        kind="expense",
        amount_minor=3000,
        from_account_id=setup["card"],
        category_id=setup["exp"],
    )
    assert await balance_of(app_client, auth_header, setup["card"]) == -3000

    r = await app_client.delete(f"/api/transactions/{tx_id}", headers=auth_header)
    assert r.status_code == 204
    assert await balance_of(app_client, auth_header, setup["card"]) == 0


async def test_balances_endpoint_requires_auth(app_client: AsyncClient):
    r = await app_client.get("/api/accounts/balances")
    assert r.status_code == 401
