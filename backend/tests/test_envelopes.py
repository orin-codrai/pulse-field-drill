"""Tests for /api/envelopes (CRUD + entries) + integration с skim-на-доход.

Покрывает:
- CRUD happy + Pydantic validation (gt=0, le=100, extra='forbid')
- reserved_minor агрегация через query-filter (B2: archived → 0 в активной
  выдаче, история entries цела)
- DELETE с entries → 409 (history immutable)
- POST /entries manual/withdraw + signed storage (PIN-C)
- POST /entries cross-workspace 404 (критичный — MF2/B1 защита)
- skim-on-income (integration с transactions/planned)
- adjustment не скимится (MF4)
- DELETE income tx → CASCADE на entries (PIN-A)
- MF11-2 + MF12-1 симметричные блокировки
"""

from datetime import datetime, timezone

import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def env_setup(app_client, provisioned_user, auth_header):
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    card = next(a["id"] for a in accounts if a["name"] == "Карта")
    cats = (await app_client.get("/api/categories", headers=auth_header)).json()
    sys_income = next(
        c["id"] for c in cats
        if c["kind"] == "income" and c["workspace_id"] is None
    )
    sys_expense = next(
        c["id"] for c in cats
        if c["kind"] == "expense" and c["workspace_id"] is None
    )
    sys_adjust = next(
        c["id"] for c in cats
        if c["kind"] == "both" and c["workspace_id"] is None
    )
    return {
        "card": card,
        "income_cat": sys_income,
        "expense_cat": sys_expense,
        "adjust_cat": sys_adjust,
    }


# ─── CRUD envelopes ──────────────────────────────────────────────────────────


async def test_post_create_with_percent(
    app_client: AsyncClient, auth_header, env_setup
):
    r = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "НЗ", "percent": 10},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "НЗ"
    assert body["percent"] == 10
    assert body["target_amount_minor"] is None
    assert body["reserved_minor"] == 0
    assert body["currency"] == "RUB"


async def test_post_create_manual_without_percent(
    app_client: AsyncClient, auth_header, env_setup
):
    r = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "Ручной", "target_amount_minor": 5000000},
    )
    assert r.status_code == 201
    assert r.json()["percent"] is None


async def test_post_percent_out_of_range_422(
    app_client: AsyncClient, auth_header, env_setup
):
    for pct in (0, 101, -5):
        r = await app_client.post(
            "/api/envelopes", headers=auth_header,
            json={"name": f"X{pct}", "percent": pct},
        )
        assert r.status_code == 422, f"percent={pct}"


async def test_post_target_zero_422(
    app_client: AsyncClient, auth_header, env_setup
):
    r = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "X", "target_amount_minor": 0},
    )
    assert r.status_code == 422


async def test_post_extra_field_rejected_422(
    app_client: AsyncClient, auth_header, env_setup
):
    """currency не принимаем (фикс RUB через server_default)."""
    r = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "X", "currency": "USD"},
    )
    assert r.status_code == 422


async def test_get_list_returns_active_only_by_default(
    app_client: AsyncClient, auth_header, env_setup
):
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "Активный", "percent": 10},
    )
    eid = r1.json()["id"]
    await app_client.patch(
        f"/api/envelopes/{eid}",
        headers=auth_header,
        json={"archived_at": "2026-06-01T00:00:00Z"},
    )

    active = (await app_client.get("/api/envelopes", headers=auth_header)).json()
    assert active == []
    all_ = (
        await app_client.get(
            "/api/envelopes?include_archived=true", headers=auth_header
        )
    ).json()
    assert len(all_) == 1


async def test_patch_percent_changes_value(
    app_client: AsyncClient, auth_header, env_setup
):
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "X", "percent": 10},
    )
    eid = r1.json()["id"]
    r = await app_client.patch(
        f"/api/envelopes/{eid}", headers=auth_header,
        json={"percent": 25},
    )
    assert r.status_code == 200
    assert r.json()["percent"] == 25


async def test_delete_without_entries_204(
    app_client: AsyncClient, auth_header, env_setup
):
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "X", "percent": 10},
    )
    eid = r1.json()["id"]
    r = await app_client.delete(f"/api/envelopes/{eid}", headers=auth_header)
    assert r.status_code == 204


async def test_delete_with_entries_409(
    app_client: AsyncClient, auth_header, env_setup
):
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "X"},
    )
    eid = r1.json()["id"]
    # manual entry → история есть.
    await app_client.post(
        f"/api/envelopes/{eid}/entries", headers=auth_header,
        json={"kind": "manual", "amount_minor": 1000},
    )
    r = await app_client.delete(f"/api/envelopes/{eid}", headers=auth_header)
    assert r.status_code == 409


async def test_cross_workspace_envelope_404(
    app_client: AsyncClient, auth_header, env_setup, db_session
):
    from app.models import Envelope
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=92001, first_name="Bob")
    )
    await db_session.commit()
    bob_env = Envelope(
        workspace_id=user_b.active_workspace_id, name="Bob's", percent=20,
    )
    db_session.add(bob_env)
    await db_session.commit()

    r = await app_client.patch(
        f"/api/envelopes/{bob_env.id}", headers=auth_header,
        json={"name": "Hijack"},
    )
    assert r.status_code == 404


# ─── Entries ────────────────────────────────────────────────────────────────


async def test_post_manual_entry_positive_storage(
    app_client: AsyncClient, auth_header, env_setup
):
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "X"},
    )
    eid = r1.json()["id"]
    r = await app_client.post(
        f"/api/envelopes/{eid}/entries", headers=auth_header,
        json={"kind": "manual", "amount_minor": 1000},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["amount_minor"] == 1000  # положительное
    assert body["kind"] == "manual"
    assert body["source_transaction_id"] is None


async def test_post_withdraw_entry_stored_negative(
    app_client: AsyncClient, auth_header, env_setup
):
    """PIN-C: payload положительный, в БД сохраняется отрицательное
    (signed semantic для прозрачной Σ)."""
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "X"},
    )
    eid = r1.json()["id"]
    r = await app_client.post(
        f"/api/envelopes/{eid}/entries", headers=auth_header,
        json={"kind": "withdraw", "amount_minor": 500},
    )
    assert r.status_code == 201
    assert r.json()["amount_minor"] == -500
    assert r.json()["kind"] == "withdraw"


async def test_entries_reserved_aggregation(
    app_client: AsyncClient, auth_header, env_setup
):
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "X"},
    )
    eid = r1.json()["id"]
    await app_client.post(
        f"/api/envelopes/{eid}/entries", headers=auth_header,
        json={"kind": "manual", "amount_minor": 1000},
    )
    await app_client.post(
        f"/api/envelopes/{eid}/entries", headers=auth_header,
        json={"kind": "manual", "amount_minor": 500},
    )
    await app_client.post(
        f"/api/envelopes/{eid}/entries", headers=auth_header,
        json={"kind": "withdraw", "amount_minor": 300},
    )
    envs = (await app_client.get("/api/envelopes", headers=auth_header)).json()
    me = next(e for e in envs if e["id"] == eid)
    assert me["reserved_minor"] == 1200  # 1000 + 500 - 300


async def test_post_entry_extra_field_rejected_422(
    app_client: AsyncClient, auth_header, env_setup
):
    """MF1: created_by_user_id server-side; payload с этим полем → 422."""
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "X"},
    )
    eid = r1.json()["id"]
    r = await app_client.post(
        f"/api/envelopes/{eid}/entries", headers=auth_header,
        json={
            "kind": "manual", "amount_minor": 1000, "created_by_user_id": 999,
        },
    )
    assert r.status_code == 422


async def test_post_entry_to_archived_envelope_409(
    app_client: AsyncClient, auth_header, env_setup
):
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "X"},
    )
    eid = r1.json()["id"]
    await app_client.patch(
        f"/api/envelopes/{eid}", headers=auth_header,
        json={"archived_at": "2026-06-01T00:00:00Z"},
    )
    r = await app_client.post(
        f"/api/envelopes/{eid}/entries", headers=auth_header,
        json={"kind": "manual", "amount_minor": 1000},
    )
    assert r.status_code == 409


async def test_get_entries_cross_workspace_404(
    app_client: AsyncClient, auth_header, env_setup, db_session
):
    """B1 critical test: денормализованный workspace_id защищает от
    утечки entries при forgotten guard. Юзер A не должен видеть entries
    юзера B."""
    from app.models import Envelope, EnvelopeEntry
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=92002, first_name="Bob")
    )
    await db_session.commit()
    bob_env = Envelope(
        workspace_id=user_b.active_workspace_id, name="Bob's", percent=20,
    )
    db_session.add(bob_env)
    await db_session.flush()
    db_session.add(
        EnvelopeEntry(
            envelope_id=bob_env.id,
            workspace_id=user_b.active_workspace_id,
            amount_minor=10000, kind="manual",
        )
    )
    await db_session.commit()

    r = await app_client.get(
        f"/api/envelopes/{bob_env.id}/entries", headers=auth_header
    )
    assert r.status_code == 404


# ─── Integration: skim on income ─────────────────────────────────────────────


async def test_income_tx_creates_skim_entry_for_active_envelope(
    app_client: AsyncClient, auth_header, env_setup
):
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "НЗ", "percent": 10},
    )
    eid = r1.json()["id"]
    tx = await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "income", "amount_minor": 100000,
            "to_account_id": env_setup["card"],
            "category_id": env_setup["income_cat"],
        },
    )
    assert tx.status_code == 201
    tx_id = tx.json()["id"]

    entries = (
        await app_client.get(f"/api/envelopes/{eid}/entries", headers=auth_header)
    ).json()
    assert len(entries) == 1
    assert entries[0]["amount_minor"] == 10000
    assert entries[0]["kind"] == "skim"
    assert entries[0]["source_transaction_id"] == tx_id


async def test_income_without_active_envelopes_no_entries(
    app_client: AsyncClient, auth_header, env_setup
):
    await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "income", "amount_minor": 50000,
            "to_account_id": env_setup["card"],
            "category_id": env_setup["income_cat"],
        },
    )
    envs = (await app_client.get("/api/envelopes", headers=auth_header)).json()
    assert envs == []


async def test_adjustment_does_not_skim(
    app_client: AsyncClient, auth_header, env_setup
):
    """MF4: adjustment с to_account_id растит баланс, но НЕ доход —
    entries не создаются, баланс счёта увеличен."""
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "НЗ", "percent": 50},  # большой пct чтобы заметить
    )
    eid = r1.json()["id"]
    await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "adjustment", "amount_minor": 10000,
            "to_account_id": env_setup["card"],
            "category_id": env_setup["adjust_cat"],
        },
    )
    entries = (
        await app_client.get(f"/api/envelopes/{eid}/entries", headers=auth_header)
    ).json()
    assert entries == []
    # Баланс счёта вырос.
    bal = (await app_client.get("/api/accounts/balances", headers=auth_header)).json()
    card_bal = next(b for b in bal if b["account_id"] == env_setup["card"])
    assert card_bal["balance_minor"] == 10000


async def test_delete_income_tx_cascades_skim_entries(
    app_client: AsyncClient, auth_header, env_setup
):
    """PIN-A: source_transaction_id ondelete='CASCADE' → DELETE income tx
    автоматически снимает skim entries → reserved синхронизируется с balance."""
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "НЗ", "percent": 10},
    )
    eid = r1.json()["id"]
    tx = await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "income", "amount_minor": 100000,
            "to_account_id": env_setup["card"],
            "category_id": env_setup["income_cat"],
        },
    )
    tx_id = tx.json()["id"]

    # Sanity: reserved=10000 до DELETE.
    envs = (await app_client.get("/api/envelopes", headers=auth_header)).json()
    assert next(e for e in envs if e["id"] == eid)["reserved_minor"] == 10000

    r = await app_client.delete(f"/api/transactions/{tx_id}", headers=auth_header)
    assert r.status_code == 204

    envs_after = (await app_client.get("/api/envelopes", headers=auth_header)).json()
    assert next(e for e in envs_after if e["id"] == eid)["reserved_minor"] == 0


async def test_confirm_income_plan_triggers_skim(
    app_client: AsyncClient, auth_header, env_setup
):
    """Plan-confirm и прямой POST tx — единый путь через skim_on_income."""
    today = datetime.now(timezone.utc).date().isoformat()
    r1 = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "НЗ", "percent": 10},
    )
    eid = r1.json()["id"]
    plan = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "income", "amount_minor": 100000,
            "category_id": env_setup["income_cat"],
            "account_id": env_setup["card"],
            "first_date": today, "recurrence": "once",
        },
    )
    pid = plan.json()["id"]
    r = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    assert r.status_code == 201

    envs = (await app_client.get("/api/envelopes", headers=auth_header)).json()
    me = next(e for e in envs if e["id"] == eid)
    assert me["reserved_minor"] == 10000


# ─── MF11-2 + MF12-1 симметричные блокировки ────────────────────────────────


async def test_delete_planned_tx_returns_409(
    app_client: AsyncClient, auth_header, env_setup
):
    """MF11-2: DELETE tx с planned_operation_id IS NOT NULL → 409
    (иначе zombie occurrence — план не вернётся в /due)."""
    today = datetime.now(timezone.utc).date().isoformat()
    plan = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 5000,
            "category_id": env_setup["expense_cat"],
            "account_id": env_setup["card"],
            "first_date": today, "recurrence": "once",
        },
    )
    pid = plan.json()["id"]
    tx = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    tx_id = tx.json()["id"]

    r = await app_client.delete(f"/api/transactions/{tx_id}", headers=auth_header)
    assert r.status_code == 409
    assert "plan" in r.json()["detail"].lower()


async def test_delete_plan_with_confirmed_tx_returns_409(
    app_client: AsyncClient, auth_header, env_setup
):
    """MF12-1: миграция 0005 шаг 9 переписала FK на RESTRICT. DELETE plan
    с confirmed tx → IntegrityError → 409. Симметрично MF11-2."""
    today = datetime.now(timezone.utc).date().isoformat()
    plan = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 5000,
            "category_id": env_setup["expense_cat"],
            "account_id": env_setup["card"],
            "first_date": today, "recurrence": "once",
        },
    )
    pid = plan.json()["id"]
    await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)

    r = await app_client.delete(f"/api/planned/{pid}", headers=auth_header)
    assert r.status_code == 409
    assert "confirmed" in r.json()["detail"].lower()


async def test_delete_unconfirmed_plan_succeeds(
    app_client: AsyncClient, auth_header, env_setup
):
    """Sanity: план без confirmed tx удаляется свободно."""
    today = datetime.now(timezone.utc).date().isoformat()
    plan = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 5000,
            "category_id": env_setup["expense_cat"],
            "account_id": env_setup["card"],
            "first_date": today, "recurrence": "once",
        },
    )
    pid = plan.json()["id"]
    r = await app_client.delete(f"/api/planned/{pid}", headers=auth_header)
    assert r.status_code == 204
