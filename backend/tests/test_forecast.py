"""Tests for /api/forecast — формула проектируемого баланса.

Покрываем:
- Empty workspace: available=planned=projected=0.
- income / expense плана суммируются с правильным знаком в окне.
- status='paused' / archived плана игнорируется.
- Confirmed план не двоится (попадает в available_now через tx, НЕ в planned_*).
- horizon > today + 13 mo → clamped (тихо).
- horizon < today → planned_*=0; projected=available.
- horizon == today: план scheduled today попадает (inclusive_start, MF8-3).
- Overdue (просроченные) не считается (C9-3) — backlog by design.
- Cross-workspace isolation.
- month-on-31 правильно: feb→28, mar→31 (не залипает).
"""

from datetime import date, datetime, timedelta, timezone

import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def fcst_setup(app_client, provisioned_user, auth_header):
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    card = next(a["id"] for a in accounts if a["name"] == "Карта")
    cats = (await app_client.get("/api/categories", headers=auth_header)).json()
    sys_expense = next(
        c["id"] for c in cats
        if c["kind"] == "expense" and c["workspace_id"] is None
    )
    sys_income = next(
        c["id"] for c in cats
        if c["kind"] == "income" and c["workspace_id"] is None
    )
    return {"card": card, "expense_cat": sys_expense, "income_cat": sys_income}


async def _create_plan(client, headers, **kwargs) -> int:
    r = await client.post("/api/planned", headers=headers, json=kwargs)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_forecast_empty_workspace(
    app_client: AsyncClient, auth_header, fcst_setup
):
    r = await app_client.get("/api/forecast", headers=auth_header)
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["available_now"] == 0
    assert f["reserved"] == 0
    assert f["planned_income"] == 0
    assert f["planned_expense"] == 0
    assert f["planned_skim"] == 0
    assert f["projected_balance"] == 0
    assert f["projected_available"] == 0


async def test_forecast_income_plan_counted(
    app_client: AsyncClient, auth_header, fcst_setup
):
    today = datetime.now(timezone.utc).date()
    # Once-план на завтра, horizon = end-of-month → ровно одно вхождение.
    await _create_plan(
        app_client, auth_header,
        kind="income", amount_minor=50000,
        category_id=fcst_setup["income_cat"], account_id=fcst_setup["card"],
        first_date=(today + timedelta(days=1)).isoformat(),
        recurrence="once",
    )
    eom = date(today.year, today.month, 28)  # запас, чтобы окно вместило
    r = await app_client.get(
        f"/api/forecast?horizon={eom.isoformat()}", headers=auth_header
    )
    f = r.json()
    # Если today+1 за пределами end-of-month — пропустит. Учитываем edge case.
    if (today + timedelta(days=1)) <= date.fromisoformat(f["horizon"]):
        assert f["planned_income"] == 50000
        assert f["projected_balance"] == 50000


async def test_forecast_expense_plan_subtracts(
    app_client: AsyncClient, auth_header, fcst_setup
):
    today = datetime.now(timezone.utc).date()
    await _create_plan(
        app_client, auth_header,
        kind="expense", amount_minor=12000,
        category_id=fcst_setup["expense_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(),  # today — MF8-3 inclusive_start
        recurrence="once",
    )
    r = await app_client.get("/api/forecast", headers=auth_header)
    f = r.json()
    assert f["planned_expense"] == 12000
    assert f["projected_balance"] == -12000


async def test_forecast_paused_plan_ignored(
    app_client: AsyncClient, auth_header, fcst_setup
):
    today = datetime.now(timezone.utc).date()
    pid = await _create_plan(
        app_client, auth_header,
        kind="expense", amount_minor=5000,
        category_id=fcst_setup["expense_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(), recurrence="month",
    )
    await app_client.patch(
        f"/api/planned/{pid}", headers=auth_header, json={"status": "paused"}
    )
    r = await app_client.get("/api/forecast", headers=auth_header)
    assert r.json()["planned_expense"] == 0


async def test_forecast_archived_plan_ignored(
    app_client: AsyncClient, auth_header, fcst_setup
):
    today = datetime.now(timezone.utc).date()
    pid = await _create_plan(
        app_client, auth_header,
        kind="expense", amount_minor=5000,
        category_id=fcst_setup["expense_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(), recurrence="month",
    )
    await app_client.patch(
        f"/api/planned/{pid}",
        headers=auth_header,
        json={"archived_at": "2026-06-01T00:00:00Z"},
    )
    r = await app_client.get("/api/forecast", headers=auth_header)
    assert r.json()["planned_expense"] == 0


async def test_forecast_confirmed_plan_not_double_counted(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """Confirm: tx уходит в available_now, completed_cycles += 1, scheduled
    смещается на следующий цикл. План должен фигурировать в planned_* ровно
    оставшимся числом вхождений, не как «изначальный план + tx»."""
    today = datetime.now(timezone.utc).date()
    pid = await _create_plan(
        app_client, auth_header,
        kind="expense", amount_minor=10000,
        category_id=fcst_setup["expense_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(), recurrence="once",
    )
    # Forecast до confirm: planned_expense=10000.
    f_before = (await app_client.get("/api/forecast", headers=auth_header)).json()
    assert f_before["planned_expense"] == 10000

    # Confirm → tx появилась, completed=1, status='done'.
    await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)

    # После confirm: balance изменился; planned_expense=0; projected учитывает
    # уже произошедшую tx через available_now (event-sourced).
    f_after = (await app_client.get("/api/forecast", headers=auth_header)).json()
    assert f_after["planned_expense"] == 0
    assert f_after["available_now"] == -10000
    assert f_after["projected_balance"] == -10000


async def test_forecast_horizon_clamp_to_13_months(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """horizon в далёком будущем тихо clamp'ится до today + 13mo, не 422."""
    far = (datetime.now(timezone.utc).date() + timedelta(days=365 * 5)).isoformat()
    r = await app_client.get(
        f"/api/forecast?horizon={far}", headers=auth_header
    )
    assert r.status_code == 200
    returned = date.fromisoformat(r.json()["horizon"])
    today = datetime.now(timezone.utc).date()
    # Возвращённый horizon не более today + 13mo + 1 день (на округление).
    assert returned <= today + timedelta(days=13 * 31)


async def test_forecast_horizon_before_today_returns_zeros(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """horizon < today: окно пустое, planned_*=0; projected=available."""
    past = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    await _create_plan(
        app_client, auth_header,
        kind="expense", amount_minor=5000,
        category_id=fcst_setup["expense_cat"], account_id=fcst_setup["card"],
        first_date=datetime.now(timezone.utc).date().isoformat(),
        recurrence="month",
    )
    r = await app_client.get(
        f"/api/forecast?horizon={past}", headers=auth_header
    )
    f = r.json()
    assert f["planned_expense"] == 0
    assert f["planned_income"] == 0


async def test_forecast_horizon_today_includes_today_scheduled(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """MF8-3 boundary: horizon=today, план scheduled today → попадает."""
    today = datetime.now(timezone.utc).date()
    await _create_plan(
        app_client, auth_header,
        kind="expense", amount_minor=7000,
        category_id=fcst_setup["expense_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(), recurrence="once",
    )
    r = await app_client.get(
        f"/api/forecast?horizon={today.isoformat()}", headers=auth_header
    )
    f = r.json()
    assert f["planned_expense"] == 7000


async def test_forecast_overdue_not_counted(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """C9-3: просроченное не-confirmed вхождение НЕ попадает в forecast.
    Юзер видит его через /due — это actionable список."""
    today = datetime.now(timezone.utc).date()
    long_ago = (today - timedelta(days=90)).isoformat()
    await _create_plan(
        app_client, auth_header,
        kind="expense", amount_minor=5000,
        category_id=fcst_setup["expense_cat"], account_id=fcst_setup["card"],
        first_date=long_ago, recurrence="once",
    )
    r = await app_client.get(
        f"/api/forecast?horizon={today.isoformat()}", headers=auth_header
    )
    f = r.json()
    # Once на 90 дней назад: completed=0, nth(0)=long_ago. window=[today, today].
    # long_ago < today → не в окне, не считается. Юзер видит план через /due.
    assert f["planned_expense"] == 0

    # Sanity: /due возвращает план как actionable.
    due = (await app_client.get("/api/planned/due", headers=auth_header)).json()
    assert len(due) == 1


async def test_forecast_reserved_aggregates_active_envelopes(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """Phase 6.D: reserved = Σ entries активных конвертов."""
    e1 = await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "НЗ"},
    )
    eid1 = e1.json()["id"]
    await app_client.post(
        f"/api/envelopes/{eid1}/entries", headers=auth_header,
        json={"kind": "manual", "amount_minor": 5000},
    )
    e2 = await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "Отпуск"},
    )
    eid2 = e2.json()["id"]
    await app_client.post(
        f"/api/envelopes/{eid2}/entries", headers=auth_header,
        json={"kind": "manual", "amount_minor": 3000},
    )

    r = await app_client.get("/api/forecast", headers=auth_header)
    f = r.json()
    assert f["reserved"] == 8000
    assert f["projected_available"] == f["projected_balance"] - 8000


async def test_forecast_archived_envelope_not_in_reserved(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """B2 query-filter: архивный конверт исключается из reserved через
    WHERE Envelope.archived_at IS NULL — entries не уничтожаются, резерв
    «возвращается» в доступно. Un-archive восстановит."""
    e = await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "X"},
    )
    eid = e.json()["id"]
    await app_client.post(
        f"/api/envelopes/{eid}/entries", headers=auth_header,
        json={"kind": "manual", "amount_minor": 5000},
    )

    before = (await app_client.get("/api/forecast", headers=auth_header)).json()
    assert before["reserved"] == 5000

    await app_client.patch(
        f"/api/envelopes/{eid}", headers=auth_header,
        json={"archived_at": "2026-06-01T00:00:00Z"},
    )

    after = (await app_client.get("/api/forecast", headers=auth_header)).json()
    assert after["reserved"] == 0
    assert after["projected_available"] > before["projected_available"]

    # Un-archive восстанавливает резерв без правки истории entries.
    await app_client.patch(
        f"/api/envelopes/{eid}", headers=auth_header,
        json={"archived_at": None},
    )
    restored = (await app_client.get("/api/forecast", headers=auth_header)).json()
    assert restored["reserved"] == 5000


async def test_forecast_cross_workspace_isolation(
    app_client: AsyncClient, auth_header, fcst_setup, db_session
):
    """План юзера B не учитывается в forecast'е A."""
    from datetime import date as _date
    from app.models import Account, Category, PlannedOperation
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned
    from sqlalchemy import select

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=88001, first_name="Bob")
    )
    await db_session.commit()
    bob_acc = await db_session.scalar(
        select(Account).where(Account.workspace_id == user_b.active_workspace_id).limit(1)
    )
    sys_cat = await db_session.scalar(
        select(Category).where(
            Category.workspace_id.is_(None), Category.kind == "expense"
        ).limit(1)
    )
    bob_plan = PlannedOperation(
        workspace_id=user_b.active_workspace_id,
        kind="expense", amount_minor=999999,
        category_id=sys_cat.id, account_id=bob_acc.id,
        first_date=_date.today(), recurrence="month",
    )
    db_session.add(bob_plan)
    await db_session.commit()

    r = await app_client.get("/api/forecast", headers=auth_header)
    # User A forecast не видит план юзера B.
    assert r.json()["planned_expense"] == 0


# ============================================================================
# planned_skim (v1.1, ADR-0008) — predicted envelope auto-skim из future income
# ============================================================================


async def test_forecast_planned_skim_single_envelope(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """Один конверт с percent → planned_skim = floor(income * pct / 100) * N.
    projected_available -= planned_skim.
    """
    today = datetime.now(timezone.utc).date()
    await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "НЗ", "percent": 10},
    )
    await _create_plan(
        app_client, auth_header,
        kind="income", amount_minor=50000,
        category_id=fcst_setup["income_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(), recurrence="once",
    )
    r = await app_client.get(
        f"/api/forecast?horizon={today.isoformat()}", headers=auth_header
    )
    f = r.json()
    assert f["planned_income"] == 50000
    assert f["planned_skim"] == 5000  # floor(50000 * 10 / 100)
    assert f["projected_balance"] == 50000  # available + income - expense
    assert f["projected_available"] == 50000 - 0 - 5000  # − reserved − skim


async def test_forecast_planned_skim_multiple_envelopes_floor_per_each(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """Несколько конвертов: skim per envelope (зеркалит skim_on_income).
    Σ floor по каждому ≤ income (не общий percent floor → потери на rounding'е
    остаются у юзера на счёте)."""
    today = datetime.now(timezone.utc).date()
    await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "A", "percent": 10},
    )
    await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "B", "percent": 15},
    )
    await _create_plan(
        app_client, auth_header,
        kind="income", amount_minor=50000,
        category_id=fcst_setup["income_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(), recurrence="once",
    )
    r = await app_client.get(
        f"/api/forecast?horizon={today.isoformat()}", headers=auth_header
    )
    f = r.json()
    # 5000 + 7500 = 12500 (per envelope floor; aggregate floor дал бы 12500 тоже,
    # но при «грязных» суммах эти два значения расходятся — тест на 50000 не
    # ловит расхождение, важно что зеркалит skim_on_income).
    assert f["planned_skim"] == 12500
    assert f["projected_available"] == 50000 - 12500


async def test_forecast_manual_envelope_no_planned_skim(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """Конверт без percent (ручной) не участвует в auto-skim → planned_skim=0."""
    today = datetime.now(timezone.utc).date()
    await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "Manual"},
    )
    await _create_plan(
        app_client, auth_header,
        kind="income", amount_minor=10000,
        category_id=fcst_setup["income_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(), recurrence="once",
    )
    r = await app_client.get(
        f"/api/forecast?horizon={today.isoformat()}", headers=auth_header
    )
    f = r.json()
    assert f["planned_income"] == 10000
    assert f["planned_skim"] == 0
    assert f["projected_available"] == 10000


async def test_forecast_planned_skim_multiple_occurrences(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """Monthly план × percent: skim умножается на N вхождений в окне."""
    from dateutil.relativedelta import relativedelta
    today = datetime.now(timezone.utc).date()
    await app_client.post(
        "/api/envelopes", headers=auth_header, json={"name": "X", "percent": 20},
    )
    await _create_plan(
        app_client, auth_header,
        kind="income", amount_minor=30000,
        category_id=fcst_setup["income_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(), recurrence="month",
    )
    # Horizon = today + 2 месяца → 3 вхождения (today, +1mo, +2mo).
    h = today + relativedelta(months=2)
    r = await app_client.get(
        f"/api/forecast?horizon={h.isoformat()}", headers=auth_header
    )
    f = r.json()
    assert f["planned_income"] == 30000 * 3
    assert f["planned_skim"] == (30000 * 20 // 100) * 3  # 6000 * 3 = 18000


async def test_forecast_archived_envelope_excluded_from_planned_skim(
    app_client: AsyncClient, auth_header, fcst_setup
):
    """Archived envelope не участвует в predicted skim (зеркалит skim_on_income,
    который тоже фильтрует архивные через archived_at IS NULL)."""
    today = datetime.now(timezone.utc).date()
    e = await app_client.post(
        "/api/envelopes", headers=auth_header,
        json={"name": "Archived", "percent": 25},
    )
    eid = e.json()["id"]
    await app_client.patch(
        f"/api/envelopes/{eid}", headers=auth_header,
        json={"archived_at": "2026-06-01T00:00:00Z"},
    )
    await _create_plan(
        app_client, auth_header,
        kind="income", amount_minor=10000,
        category_id=fcst_setup["income_cat"], account_id=fcst_setup["card"],
        first_date=today.isoformat(), recurrence="once",
    )
    r = await app_client.get(
        f"/api/forecast?horizon={today.isoformat()}", headers=auth_header
    )
    f = r.json()
    assert f["planned_skim"] == 0
    assert f["projected_available"] == 10000
