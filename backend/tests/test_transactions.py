"""Tests for /api/transactions router.

Покрываем все 4 kind, валидацию XOR/RESTRICT-FK, иммутабельность PATCH,
cross-user 404, archived-account 422.
"""

import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def user_setup(app_client, provisioned_user, auth_header, db_session):
    """User c 2 default accounts + 1 expense-категория. Возвращает dict
    с id'шниками для удобной сборки payload'ов в тестах."""
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    card_id = next(a["id"] for a in accounts if a["name"] == "Карта")
    cash_id = next(a["id"] for a in accounts if a["name"] == "Наличные")

    cats = (await app_client.get("/api/categories", headers=auth_header)).json()
    expense_cat = next(c for c in cats if c["kind"] == "expense" and c["workspace_id"] is None)
    income_cat = next(c for c in cats if c["kind"] == "income")
    adjust_cat = next(c for c in cats if c["kind"] == "both")

    return {
        "card_id": card_id,
        "cash_id": cash_id,
        "expense_cat_id": expense_cat["id"],
        "income_cat_id": income_cat["id"],
        "adjust_cat_id": adjust_cat["id"],
    }


# ─── POST: happy path для всех kind ──────────────────────────────────────────


async def test_post_expense(app_client: AsyncClient, auth_header, user_setup):
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 3000,
            "from_account_id": user_setup["card_id"],
            "category_id": user_setup["expense_cat_id"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "expense"
    assert body["amount_minor"] == 3000
    assert body["to_account_id"] is None


async def test_post_income(app_client: AsyncClient, auth_header, user_setup):
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 50000,
            "to_account_id": user_setup["card_id"],
            "category_id": user_setup["income_cat_id"],
        },
    )
    assert r.status_code == 201, r.text


async def test_post_transfer(app_client: AsyncClient, auth_header, user_setup):
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "transfer",
            "amount_minor": 1000,
            "from_account_id": user_setup["card_id"],
            "to_account_id": user_setup["cash_id"],
        },
    )
    assert r.status_code == 201, r.text


async def test_post_adjustment_in(app_client: AsyncClient, auth_header, user_setup):
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "adjustment",
            "amount_minor": 500,
            "to_account_id": user_setup["card_id"],
            "category_id": user_setup["adjust_cat_id"],
        },
    )
    assert r.status_code == 201, r.text


# ─── POST: CHECK violations → 422 ─────────────────────────────────────────────


async def test_post_transfer_self_returns_422(
    app_client: AsyncClient, auth_header, user_setup
):
    """from == to запрещён CHECK'ом (transactions_kind_fields_chk)."""
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "transfer",
            "amount_minor": 100,
            "from_account_id": user_setup["card_id"],
            "to_account_id": user_setup["card_id"],
        },
    )
    assert r.status_code == 422


async def test_post_adjustment_both_accounts_returns_422(
    app_client: AsyncClient, auth_header, user_setup
):
    """XOR на adjustment: оба FK non-null запрещены."""
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "adjustment",
            "amount_minor": 500,
            "from_account_id": user_setup["card_id"],
            "to_account_id": user_setup["cash_id"],
            "category_id": user_setup["adjust_cat_id"],
        },
    )
    assert r.status_code == 422


async def test_post_expense_without_category_returns_422(
    app_client: AsyncClient, auth_header, user_setup
):
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 100,
            "from_account_id": user_setup["card_id"],
        },
    )
    assert r.status_code == 422


async def test_post_transfer_with_category_returns_422(
    app_client: AsyncClient, auth_header, user_setup
):
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "transfer",
            "amount_minor": 100,
            "from_account_id": user_setup["card_id"],
            "to_account_id": user_setup["cash_id"],
            "category_id": user_setup["expense_cat_id"],
        },
    )
    assert r.status_code == 422


async def test_post_zero_amount_returns_422(
    app_client: AsyncClient, auth_header, user_setup
):
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 0,
            "from_account_id": user_setup["card_id"],
            "category_id": user_setup["expense_cat_id"],
        },
    )
    assert r.status_code == 422


# ─── POST: application-level validation → 422 ─────────────────────────────────


async def test_post_with_foreign_account_returns_422(
    app_client: AsyncClient, auth_header, user_setup, db_session
):
    """from_account_id указывает на чужой счёт → 422 (не 500 от FK CHECK)."""
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=77777, first_name="Bob")
    )
    await db_session.commit()
    from sqlalchemy import select

    from app.models import Account

    bob_acc = await db_session.scalar(
        select(Account.id)
        .where(Account.workspace_id == user_b.active_workspace_id)
        .limit(1)
    )

    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 100,
            "from_account_id": bob_acc,
            "category_id": user_setup["expense_cat_id"],
        },
    )
    assert r.status_code == 422


async def test_post_with_archived_account_returns_422(
    app_client: AsyncClient, auth_header, user_setup
):
    await app_client.patch(
        f"/api/accounts/{user_setup['card_id']}",
        headers=auth_header,
        json={"archived_at": "2026-05-01T00:00:00Z"},
    )
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 100,
            "from_account_id": user_setup["card_id"],
            "category_id": user_setup["expense_cat_id"],
        },
    )
    assert r.status_code == 422


async def test_post_with_archived_category_returns_422(
    app_client: AsyncClient, auth_header, user_setup
):
    """Зеркало для archived-account guard, но на category. Создаём свою
    expense-категорию (системную архивировать нельзя через PATCH/403),
    архивируем её, потом пробуем создать транзакцию с ней."""
    new = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "Хобби-tx", "kind": "expense"},
    )
    cid = new.json()["id"]
    await app_client.patch(
        f"/api/categories/{cid}",
        headers=auth_header,
        json={"archived_at": "2026-05-01T00:00:00Z"},
    )
    r = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 100,
            "from_account_id": user_setup["card_id"],
            "category_id": cid,
        },
    )
    assert r.status_code == 422


# ─── GET list + filter ────────────────────────────────────────────────────────


async def test_list_filters_by_kind(
    app_client: AsyncClient, auth_header, user_setup
):
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 100,
            "from_account_id": user_setup["card_id"],
            "category_id": user_setup["expense_cat_id"],
        },
    )
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 50000,
            "to_account_id": user_setup["card_id"],
            "category_id": user_setup["income_cat_id"],
        },
    )
    r = await app_client.get("/api/transactions?kind=expense", headers=auth_header)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["kind"] == "expense"


# ─── PATCH + immutability ─────────────────────────────────────────────────────


async def test_patch_note(app_client: AsyncClient, auth_header, user_setup):
    create = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 100,
            "from_account_id": user_setup["card_id"],
            "category_id": user_setup["expense_cat_id"],
        },
    )
    tx_id = create.json()["id"]
    r = await app_client.patch(
        f"/api/transactions/{tx_id}",
        headers=auth_header,
        json={"note": "coffee"},
    )
    assert r.status_code == 200
    assert r.json()["note"] == "coffee"


async def test_patch_amount_rejected_422(
    app_client: AsyncClient, auth_header, user_setup
):
    """amount_minor — иммутабельное поле; whitelist extra='forbid'."""
    create = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 100,
            "from_account_id": user_setup["card_id"],
            "category_id": user_setup["expense_cat_id"],
        },
    )
    tx_id = create.json()["id"]
    r = await app_client.patch(
        f"/api/transactions/{tx_id}",
        headers=auth_header,
        json={"amount_minor": 999},
    )
    assert r.status_code == 422


# ─── DELETE + cross-user 404 ─────────────────────────────────────────────────


async def test_delete_happy(app_client: AsyncClient, auth_header, user_setup):
    create = await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 100,
            "from_account_id": user_setup["card_id"],
            "category_id": user_setup["expense_cat_id"],
        },
    )
    tx_id = create.json()["id"]
    r = await app_client.delete(f"/api/transactions/{tx_id}", headers=auth_header)
    assert r.status_code == 204

    after = await app_client.get(f"/api/transactions/{tx_id}", headers=auth_header)
    assert after.status_code == 404


async def test_cross_user_404(
    app_client: AsyncClient, auth_header, user_setup, db_session
):
    """User A смотрит транзакцию user B → 404 (не 403)."""
    from app.models import Account, Transaction
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=66666, first_name="Bob")
    )
    await db_session.commit()
    from sqlalchemy import select

    bob_card = await db_session.scalar(
        select(Account).where(Account.workspace_id == user_b.active_workspace_id).limit(1)
    )
    # Создать одну expense у Боба через любую системную expense-категорию.
    bob_tx = Transaction(
        workspace_id=user_b.active_workspace_id,
        kind="expense",
        amount_minor=100,
        from_account_id=bob_card.id,
        category_id=user_setup["expense_cat_id"],
    )
    db_session.add(bob_tx)
    await db_session.commit()

    r = await app_client.get(
        f"/api/transactions/{bob_tx.id}", headers=auth_header
    )
    assert r.status_code == 404

    r2 = await app_client.delete(
        f"/api/transactions/{bob_tx.id}", headers=auth_header
    )
    assert r2.status_code == 404
