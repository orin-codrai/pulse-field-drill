"""Tests for /api/goals router + /goals/{id}/progress.

Покрываем (must-fix из plan v2):
- #1: goal progress unlinked uses ONLY system «Зарплата»; user-owned копия
  и категория «Корректировка» НЕ считаются; missing seed → 500.
- #2: cross-user linked_account_id → 422 (не leak, не 500).
- CRUD happy + cross-user 404.
"""

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, text


@pytest_asyncio.fixture
async def setup(app_client, provisioned_user, auth_header):
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    card_id = next(a["id"] for a in accounts if a["name"] == "Карта")
    cash_id = next(a["id"] for a in accounts if a["name"] == "Наличные")
    cats = (await app_client.get("/api/categories", headers=auth_header)).json()
    zarplata = next(
        c for c in cats if c["name"] == "Зарплата" and c["workspace_id"] is None
    )
    korrektirovka = next(
        c for c in cats if c["name"] == "Корректировка" and c["workspace_id"] is None
    )
    return {
        "card": card_id,
        "cash": cash_id,
        "zarplata": zarplata["id"],
        "korrektirovka": korrektirovka["id"],
    }


# ─── CRUD ─────────────────────────────────────────────────────────────────────


async def test_post_create_with_linked_account(
    app_client: AsyncClient, auth_header, setup
):
    r = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={
            "name": "Подушка",
            "target_amount_minor": 100000,
            "linked_account_id": setup["card"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Подушка"
    assert body["linked_account_id"] == setup["card"]


async def test_post_create_unlinked(
    app_client: AsyncClient, auth_header, setup
):
    r = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={"name": "Quest", "target_amount_minor": 50000},
    )
    assert r.status_code == 201
    assert r.json()["linked_account_id"] is None


async def test_post_zero_target_rejected_422(
    app_client: AsyncClient, auth_header, setup
):
    r = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={"name": "X", "target_amount_minor": 0},
    )
    assert r.status_code == 422


async def test_post_extra_field_rejected_422(
    app_client: AsyncClient, auth_header, setup
):
    r = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={"name": "X", "target_amount_minor": 100, "user_id": 999},
    )
    assert r.status_code == 422


# ─── Cross-user FK guards (must-fix #2) ───────────────────────────────────────


async def test_post_with_foreign_linked_account_returns_422(
    app_client: AsyncClient, auth_header, setup, db_session
):
    """Чужой linked_account_id → 422 (не 500/leak)."""
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=55555, first_name="Bob")
    )
    await db_session.commit()
    from app.models import Account

    bob_acc = await db_session.scalar(
        select(Account.id).where(Account.workspace_id == user_b.active_workspace_id).limit(1)
    )

    r = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={"name": "Hijack", "target_amount_minor": 100, "linked_account_id": bob_acc},
    )
    assert r.status_code == 422


async def test_post_with_archived_linked_account_returns_422(
    app_client: AsyncClient, auth_header, setup
):
    await app_client.patch(
        f"/api/accounts/{setup['card']}",
        headers=auth_header,
        json={"archived_at": "2026-05-01T00:00:00Z"},
    )
    r = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={
            "name": "X",
            "target_amount_minor": 100,
            "linked_account_id": setup["card"],
        },
    )
    assert r.status_code == 422


# ─── List / cross-user 404 ────────────────────────────────────────────────────


async def test_list_returns_only_own_goals(
    app_client: AsyncClient, auth_header, setup, db_session
):
    await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={"name": "Mine", "target_amount_minor": 100},
    )
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=44444, first_name="Bob")
    )
    await db_session.commit()
    from app.models import Goal

    bob_goal = Goal(workspace_id=user_b.active_workspace_id, name="Bob's", target_amount_minor=999)
    db_session.add(bob_goal)
    await db_session.commit()

    r = await app_client.get("/api/goals", headers=auth_header)
    goals = r.json()
    names = [g["name"] for g in goals]
    assert "Mine" in names
    assert "Bob's" not in names


async def test_patch_other_users_goal_returns_404(
    app_client: AsyncClient, auth_header, setup, db_session
):
    from app.models import Goal
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=33333, first_name="Bob")
    )
    await db_session.commit()
    bob_goal = Goal(workspace_id=user_b.active_workspace_id, name="Bob's", target_amount_minor=999)
    db_session.add(bob_goal)
    await db_session.commit()

    r = await app_client.patch(
        f"/api/goals/{bob_goal.id}", headers=auth_header, json={"name": "X"}
    )
    assert r.status_code == 404


async def test_delete_happy_and_cross_user_404(
    app_client: AsyncClient, auth_header, setup, db_session
):
    new = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={"name": "X", "target_amount_minor": 100},
    )
    gid = new.json()["id"]
    r = await app_client.delete(f"/api/goals/{gid}", headers=auth_header)
    assert r.status_code == 204

    # cross-user delete
    from app.models import Goal
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=22222, first_name="Bob")
    )
    await db_session.commit()
    bob_goal = Goal(workspace_id=user_b.active_workspace_id, name="B", target_amount_minor=1)
    db_session.add(bob_goal)
    await db_session.commit()
    r2 = await app_client.delete(f"/api/goals/{bob_goal.id}", headers=auth_header)
    assert r2.status_code == 404


# ─── Progress ─────────────────────────────────────────────────────────────────


async def test_progress_linked_uses_account_balance(
    app_client: AsyncClient, auth_header, setup
):
    """linked: progress = balance of linked account, не сумма всех income."""
    # Создаём income на «Карта» → balance = 50000.
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 50000,
            "to_account_id": setup["card"],
            "category_id": setup["zarplata"],
        },
    )
    # Goal linked на «Карта», target 100000.
    new = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={
            "name": "Подушка",
            "target_amount_minor": 100000,
            "linked_account_id": setup["card"],
        },
    )
    gid = new.json()["id"]
    r = await app_client.get(f"/api/goals/{gid}/progress", headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_minor"] == 50000
    assert body["target_minor"] == 100000
    assert body["percent"] == 50.0


async def test_progress_unlinked_uses_zarplata_only(
    app_client: AsyncClient, auth_header, setup
):
    """Только income в системной «Зарплата» считается. Корректировка и
    юзеровская копия — нет."""
    # Создаём goal сначала, потом транзакции — чтобы все попадали под occurred_at >= goal.created_at.
    new = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={"name": "Quest", "target_amount_minor": 100000},
    )
    gid = new.json()["id"]

    # Income в системной «Зарплата» — считается.
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 30000,
            "to_account_id": setup["card"],
            "category_id": setup["zarplata"],
        },
    )
    # Income в «Корректировка» (kind='both', можно использовать для kind='income' tx) — НЕ считается.
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 7777,
            "to_account_id": setup["card"],
            "category_id": setup["korrektirovka"],
        },
    )
    # Юзеровская «Зарплата» — НЕ считается (намеренная collision с system name).
    own = await app_client.post(
        "/api/categories",
        headers=auth_header,
        json={"name": "Зарплата", "kind": "income"},
    )
    own_id = own.json()["id"]
    await app_client.post(
        "/api/transactions",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 9999,
            "to_account_id": setup["card"],
            "category_id": own_id,
        },
    )

    r = await app_client.get(f"/api/goals/{gid}/progress", headers=auth_header)
    assert r.status_code == 200, r.text
    assert r.json()["current_minor"] == 30000


async def test_progress_unlinked_no_zarplata_raises_500(
    app_client: AsyncClient, auth_header, setup, db_session
):
    """Если seed повреждён (системная «Зарплата» удалена) — 500, не 0%."""
    new = await app_client.post(
        "/api/goals",
        headers=auth_header,
        json={"name": "X", "target_amount_minor": 100},
    )
    gid = new.json()["id"]

    # Вручную сносим системную «Зарплата» (имитация broken seed).
    await db_session.execute(
        text("DELETE FROM categories WHERE workspace_id IS NULL AND name = 'Зарплата'")
    )
    await db_session.commit()

    r = await app_client.get(f"/api/goals/{gid}/progress", headers=auth_header)
    assert r.status_code == 500
