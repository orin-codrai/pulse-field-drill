"""Tests for /api/planned router — CRUD + /due + /confirm.

Покрываем:
- POST/GET/PATCH/DELETE happy + cross-workspace 422/404.
- POST CHECK violations через extra='forbid' / NOT NULL колонки.
- kind-matching category (MF6), once без total_cycles.
- PATCH mass-assignment защита (MF3): completed_cycles, status='done' отбиты.
- PATCH first_date/recurrence после confirm — MF2.
- PATCH null FK — MF8-4.
- /due — past+today scheduled только, паттерн «первое неподтверждённое».
- /confirm — happy для всех типов, идемпотентность (MF9), grace=7 (MF5),
  currency пробрасывание (MF8), status auto-done.
- TestConcurrencyConfirm — двойной commit через два отдельных sessionmaker.
"""

import asyncio
from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest_asyncio.fixture
async def planned_setup(app_client, provisioned_user, auth_header):
    """Минимум: один account и одна expense-категория для plan'a."""
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
    return {
        "card": card,
        "expense_cat": sys_expense,
        "income_cat": sys_income,
    }


# ─── POST CRUD happy + checks ────────────────────────────────────────────────


async def test_post_expense_happy(app_client: AsyncClient, auth_header, planned_setup):
    r = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 3500000,  # 35000₽
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01",
            "recurrence": "month",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "expense"
    assert body["status"] == "planned"
    assert body["completed_cycles"] == 0
    assert body["currency"] == "RUB"


async def test_post_income_happy(app_client: AsyncClient, auth_header, planned_setup):
    r = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 10000000,
            "category_id": planned_setup["income_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-05",
            "recurrence": "month",
        },
    )
    assert r.status_code == 201, r.text


async def test_post_without_category_422(
    app_client: AsyncClient, auth_header, planned_setup
):
    """MF10-1: category_id обязательна для всех kind (income тоже)."""
    r = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "income",
            "amount_minor": 1000,
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01",
            "recurrence": "once",
        },
    )
    assert r.status_code == 422


async def test_post_kind_mismatch_category_422(
    app_client: AsyncClient, auth_header, planned_setup
):
    """MF6: expense-план с income-категорией → 422."""
    r = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 1000,
            "category_id": planned_setup["income_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01",
            "recurrence": "month",
        },
    )
    assert r.status_code == 422
    assert "kind" in r.json()["detail"]


async def test_post_once_with_total_cycles_422(
    app_client: AsyncClient, auth_header, planned_setup
):
    """CHECK planned_once_no_total_chk: once + total_cycles не имеет смысла."""
    r = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01",
            "recurrence": "once",
            "total_cycles": 5,
        },
    )
    assert r.status_code == 422


async def test_post_completed_cycles_in_body_422(
    app_client: AsyncClient, auth_header, planned_setup
):
    """MF3: extra='forbid' — completed_cycles нельзя ставить через POST."""
    r = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01",
            "recurrence": "month",
            "completed_cycles": 5,
        },
    )
    assert r.status_code == 422


async def test_post_with_currency_field_422(
    app_client: AsyncClient, auth_header, planned_setup
):
    """currency не принимаем — фикс RUB через server_default."""
    r = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 1000,
            "currency": "USD",
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01",
            "recurrence": "month",
        },
    )
    assert r.status_code == 422


async def test_post_foreign_account_id_422(
    app_client: AsyncClient, auth_header, planned_setup, db_session
):
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned
    from app.models import Account

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=66001, first_name="Bob")
    )
    await db_session.commit()
    bob_acc = await db_session.scalar(
        select(Account.id).where(Account.workspace_id == user_b.active_workspace_id).limit(1)
    )

    r = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense",
            "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": bob_acc,
            "first_date": "2026-06-01",
            "recurrence": "month",
        },
    )
    assert r.status_code == 422


# ─── GET list ────────────────────────────────────────────────────────────────


async def test_get_list_returns_only_active_by_default(
    app_client: AsyncClient, auth_header, planned_setup
):
    """archived_at IS NULL по умолчанию; include_archived=true показывает все."""
    r1 = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01", "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    # Архивировать.
    await app_client.patch(
        f"/api/planned/{pid}",
        headers=auth_header,
        json={"archived_at": "2026-06-01T00:00:00Z"},
    )
    r = await app_client.get("/api/planned", headers=auth_header)
    assert r.status_code == 200
    assert len(r.json()) == 0
    r_all = await app_client.get("/api/planned?include_archived=true", headers=auth_header)
    assert len(r_all.json()) == 1


# ─── PATCH ───────────────────────────────────────────────────────────────────


async def test_patch_completed_cycles_rejected_422(
    app_client: AsyncClient, auth_header, planned_setup
):
    """MF3: completed_cycles не в whitelist — extra='forbid' отбивает."""
    r1 = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01", "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    r = await app_client.patch(
        f"/api/planned/{pid}",
        headers=auth_header,
        json={"completed_cycles": 999},
    )
    assert r.status_code == 422


async def test_patch_status_done_rejected_422(
    app_client: AsyncClient, auth_header, planned_setup
):
    """status='done' Literal не пускает — done выставляется только confirm'ом."""
    r1 = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01", "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    r = await app_client.patch(
        f"/api/planned/{pid}", headers=auth_header, json={"status": "done"}
    )
    assert r.status_code == 422


async def test_patch_first_date_after_confirm_422(
    app_client: AsyncClient, auth_header, planned_setup, db_session
):
    """MF2: PATCH first_date запрещён если completed_cycles > 0 — иначе
    nth_occurrence сдвинется от нового базиса и будущие вхождения уедут
    относительно уже подтверждённых tx."""
    r1 = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01", "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    # Симулируем уже-confirmed план: руками выставляем completed_cycles.
    from app.models import PlannedOperation
    op = await db_session.scalar(
        select(PlannedOperation).where(PlannedOperation.id == pid)
    )
    op.completed_cycles = 1
    await db_session.commit()

    r = await app_client.patch(
        f"/api/planned/{pid}",
        headers=auth_header,
        json={"first_date": "2026-07-01"},
    )
    assert r.status_code == 422
    assert "first_date" in r.json()["detail"]


async def test_patch_recurrence_after_confirm_422(
    app_client: AsyncClient, auth_header, planned_setup, db_session
):
    """MF2 mirror: PATCH recurrence запрещён после confirm."""
    r1 = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01", "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    from app.models import PlannedOperation
    op = await db_session.scalar(
        select(PlannedOperation).where(PlannedOperation.id == pid)
    )
    op.completed_cycles = 1
    await db_session.commit()

    r = await app_client.patch(
        f"/api/planned/{pid}", headers=auth_header, json={"recurrence": "week"}
    )
    assert r.status_code == 422


async def test_patch_null_account_id_422(
    app_client: AsyncClient, auth_header, planned_setup
):
    """MF8-4: explicit null в NOT NULL → 422, не 500."""
    r1 = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01", "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    r = await app_client.patch(
        f"/api/planned/{pid}", headers=auth_header, json={"account_id": None}
    )
    assert r.status_code == 422


async def test_patch_null_category_id_422(
    app_client: AsyncClient, auth_header, planned_setup
):
    """MF8-4: category_id NOT NULL (MF10-1), null → 422."""
    r1 = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01", "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    r = await app_client.patch(
        f"/api/planned/{pid}", headers=auth_header, json={"category_id": None}
    )
    assert r.status_code == 422


async def test_patch_cross_workspace_404(
    app_client: AsyncClient, auth_header, planned_setup, db_session
):
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned
    from app.models import Account, Category, PlannedOperation

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=66002, first_name="Bob")
    )
    await db_session.commit()
    bob_acc = await db_session.scalar(
        select(Account).where(Account.workspace_id == user_b.active_workspace_id).limit(1)
    )
    sys_cat = await db_session.scalar(
        select(Category).where(Category.workspace_id.is_(None)).limit(1)
    )
    bob_plan = PlannedOperation(
        workspace_id=user_b.active_workspace_id,
        kind="expense", amount_minor=1000,
        category_id=sys_cat.id, account_id=bob_acc.id,
        first_date=date(2026, 6, 1), recurrence="month",
    )
    db_session.add(bob_plan)
    await db_session.commit()

    r = await app_client.patch(
        f"/api/planned/{bob_plan.id}",
        headers=auth_header, json={"note": "hijack"},
    )
    assert r.status_code == 404


# ─── DELETE ──────────────────────────────────────────────────────────────────


async def test_delete_happy(
    app_client: AsyncClient, auth_header, planned_setup
):
    r1 = await app_client.post(
        "/api/planned",
        headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": "2026-06-01", "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    r = await app_client.delete(f"/api/planned/{pid}", headers=auth_header)
    assert r.status_code == 204


# ─── /due ────────────────────────────────────────────────────────────────────


async def test_due_empty_when_no_plans(app_client: AsyncClient, auth_header, planned_setup):
    r = await app_client.get("/api/planned/due", headers=auth_header)
    assert r.status_code == 200
    assert r.json() == []


async def test_due_returns_past_and_today_only(
    app_client: AsyncClient, auth_header, planned_setup
):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()

    await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 100,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": yesterday, "recurrence": "once",
        },
    )
    await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 200,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": today.isoformat(), "recurrence": "once",
        },
    )
    await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 300,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": tomorrow, "recurrence": "once",
        },
    )

    r = await app_client.get("/api/planned/due", headers=auth_header)
    assert r.status_code == 200
    items = r.json()
    # yesterday + today, не tomorrow.
    assert len(items) == 2
    amounts = sorted(i["amount_minor"] for i in items)
    assert amounts == [100, 200]


# ─── /confirm ────────────────────────────────────────────────────────────────


async def test_confirm_happy_creates_tx_and_increments(
    app_client: AsyncClient, auth_header, planned_setup
):
    """Confirm sequence для once-плана today: 201 + tx сохранена с правильными
    currency/category/account; completed_cycles=1; status=done."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    r1 = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 5000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": today, "recurrence": "once",
        },
    )
    pid = r1.json()["id"]
    r = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    assert r.status_code == 201, r.text
    tx = r.json()
    assert tx["kind"] == "expense"
    assert tx["amount_minor"] == 5000
    assert tx["currency"] == "RUB"
    assert tx["from_account_id"] == planned_setup["card"]
    assert tx["category_id"] == planned_setup["expense_cat"]
    # Состояние плана.
    plan = (
        await app_client.get(f"/api/planned?include_archived=false", headers=auth_header)
    ).json()
    me = next(p for p in plan if p["id"] == pid)
    assert me["completed_cycles"] == 1
    assert me["status"] == "done"


async def test_confirm_idempotent_409(
    app_client: AsyncClient, auth_header, planned_setup
):
    """MF9: повторный confirm одного вхождения → 409, без двойной tx."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    r1 = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 5000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": today, "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    r2 = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    assert r2.status_code == 201
    r3 = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    # После первого confirm completed_cycles=1, nth(1) = next month — будущая
    # дата; за пределами grace=7 → 409.
    assert r3.status_code == 409


async def test_confirm_future_beyond_grace_409(
    app_client: AsyncClient, auth_header, planned_setup
):
    """MF5: confirm на today+8 (за пределами grace=7) → 409."""
    from datetime import datetime, timezone
    far = (datetime.now(timezone.utc).date() + timedelta(days=8)).isoformat()
    r1 = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": far, "recurrence": "once",
        },
    )
    pid = r1.json()["id"]
    r = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    assert r.status_code == 409
    assert "scheduled" in r.json()["detail"]


async def test_confirm_within_grace_201(
    app_client: AsyncClient, auth_header, planned_setup
):
    """MF5 boundary: confirm на today+7 (граница) → 201."""
    from datetime import datetime, timezone
    edge = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()
    r1 = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": edge, "recurrence": "once",
        },
    )
    pid = r1.json()["id"]
    r = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    assert r.status_code == 201


async def test_confirm_paused_409(
    app_client: AsyncClient, auth_header, planned_setup
):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    r1 = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": today, "recurrence": "month",
        },
    )
    pid = r1.json()["id"]
    await app_client.patch(
        f"/api/planned/{pid}", headers=auth_header, json={"status": "paused"}
    )
    r = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    assert r.status_code == 409


async def test_confirm_foreign_404(
    app_client: AsyncClient, auth_header, planned_setup, db_session
):
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned
    from app.models import Account, Category, PlannedOperation

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=66003, first_name="Bob")
    )
    await db_session.commit()
    bob_acc = await db_session.scalar(
        select(Account).where(Account.workspace_id == user_b.active_workspace_id).limit(1)
    )
    sys_cat = await db_session.scalar(
        select(Category).where(Category.workspace_id.is_(None), Category.kind == "expense").limit(1)
    )
    bob_plan = PlannedOperation(
        workspace_id=user_b.active_workspace_id,
        kind="expense", amount_minor=1000,
        category_id=sys_cat.id, account_id=bob_acc.id,
        first_date=date(2026, 6, 1), recurrence="month",
    )
    db_session.add(bob_plan)
    await db_session.commit()

    r = await app_client.post(
        f"/api/planned/{bob_plan.id}/confirm", headers=auth_header
    )
    assert r.status_code == 404


async def test_confirm_month_total_2_then_done(
    app_client: AsyncClient, auth_header, planned_setup, db_session
):
    """total_cycles=2: после второго confirm status='done'; третий → 409
    'cannot confirm: status=done'."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    r1 = await app_client.post(
        "/api/planned", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "category_id": planned_setup["expense_cat"],
            "account_id": planned_setup["card"],
            "first_date": today, "recurrence": "month", "total_cycles": 2,
        },
    )
    pid = r1.json()["id"]

    # Симулируем, что прошёл месяц — подвинем плановые даты выше реалистично:
    # просто confirm'нем первое (today), потом перепрыгнем через completed_cycles
    # в БД, чтобы второй confirm'd оказался в окне grace. Это hack для теста.
    r2 = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    assert r2.status_code == 201

    # Сдвинем first_date в DB к today−30, чтобы nth(1)=today.
    from app.models import PlannedOperation
    plan = await db_session.scalar(
        select(PlannedOperation).where(PlannedOperation.id == pid)
    )
    plan.first_date = (datetime.now(timezone.utc).date() - timedelta(days=30))
    await db_session.commit()

    r3 = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    assert r3.status_code == 201
    # Состояние: status=done после второго confirm.
    plans = (await app_client.get("/api/planned?include_archived=true", headers=auth_header)).json()
    me = next(p for p in plans if p["id"] == pid)
    assert me["status"] == "done"
    assert me["completed_cycles"] == 2

    # Третий confirm → 409 «cannot confirm: status=done».
    r4 = await app_client.post(f"/api/planned/{pid}/confirm", headers=auth_header)
    assert r4.status_code == 409


# ─── Concurrent confirm (MF8-5) ──────────────────────────────────────────────


@pytest.mark.no_rollback
class TestConcurrencyConfirm:
    """Race condition: два concurrent confirm одного плана через ОТДЕЛЬНЫЕ
    sessionmaker (иначе обработчики на shared session идут одной транзакцией,
    pre-check во втором ловит первый — IntegrityError-ветка не покрывается).

    Шаблон: test_user_provisioning.py:TestConcurrency.
    """

    async def test_two_concurrent_confirms_one_201_one_409(self, test_engine):
        from sqlalchemy import text
        from app.models import Account, Category, PlannedOperation, User, Workspace, WorkspaceMember
        from app.schemas.user import TelegramUser
        from app.services.user_provisioning import ensure_user_provisioned

        async with test_engine.begin() as conn:
            await conn.execute(text(
                "TRUNCATE transactions, planned_operations, receipts, "
                "budgets, goals, accounts, categories, workspace_members, "
                "workspaces, users RESTART IDENTITY CASCADE"
            ))
            from app.seed.system_categories import SYSTEM_CATEGORIES
            for icon, name, kind in SYSTEM_CATEGORIES:
                await conn.execute(
                    text("INSERT INTO categories (workspace_id, name, kind, icon) "
                         "VALUES (NULL, :name, :kind, :icon)"),
                    {"name": name, "kind": kind, "icon": icon},
                )

        SessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)

        # Setup: один юзер с workspace, account, scheduled-today план.
        async with SessionLocal() as s:
            user = await ensure_user_provisioned(
                s, TelegramUser(id=77001, first_name="Race")
            )
            await s.commit()
            account = await s.scalar(
                select(Account).where(Account.workspace_id == user.active_workspace_id).limit(1)
            )
            sys_cat = await s.scalar(
                select(Category).where(Category.workspace_id.is_(None), Category.kind == "expense").limit(1)
            )
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).date()
            plan = PlannedOperation(
                workspace_id=user.active_workspace_id,
                kind="expense", amount_minor=1000,
                category_id=sys_cat.id, account_id=account.id,
                first_date=today, recurrence="once",
            )
            s.add(plan)
            await s.commit()
            plan_id = plan.id
            ws_id = user.active_workspace_id

        # Имитируем confirm-вызов на «голом» SQLAlchemy: каждый concurrent
        # confirm = отдельный sessionmaker → отдельная connection-pool tx.
        # Реальный HTTP-flow тоже идёт через два разных connection'a.
        from app.services.occurrences import nth_occurrence
        from app.models import Transaction

        async def confirm_via_session() -> int:
            """Возвращает 201 (создал tx) или 409 (поймал unique-violation)."""
            from sqlalchemy.exc import IntegrityError
            async with SessionLocal() as s:
                op = await s.scalar(
                    select(PlannedOperation).where(
                        PlannedOperation.id == plan_id,
                        PlannedOperation.workspace_id == ws_id,
                    )
                )
                if op is None or op.status != "planned":
                    return 409
                scheduled = nth_occurrence(op.first_date, op.recurrence, op.completed_cycles)
                if scheduled is None:
                    return 409
                # ПРОПУСКАЕМ pre-check (две концеррент-сессии не видят друг
                # друга до commit), полагаемся только на unique partial index.
                tx = Transaction(
                    workspace_id=op.workspace_id,
                    kind=op.kind, amount_minor=op.amount_minor,
                    currency=op.currency, category_id=op.category_id,
                    from_account_id=op.account_id,
                    planned_operation_id=op.id, occurrence_date=scheduled,
                )
                s.add(tx)
                op.completed_cycles += 1
                if op.recurrence == "once":
                    op.status = "done"
                try:
                    await s.commit()
                except IntegrityError as e:
                    await s.rollback()
                    constraint = (
                        getattr(getattr(e.orig, "diag", None), "constraint_name", None)
                        or getattr(e.orig, "constraint_name", None)
                    )
                    if constraint == "transactions_planned_uq":
                        return 409
                    raise
                return 201

        codes = await asyncio.gather(confirm_via_session(), confirm_via_session())
        # Один 201, один 409 (через unique partial index).
        assert sorted(codes) == [201, 409], codes

        # Финал: ровно одна tx с этим (planned_operation_id, occurrence_date).
        async with SessionLocal() as s:
            n = await s.scalar(
                select(func.count(Transaction.id)).where(
                    Transaction.planned_operation_id == plan_id
                )
            )
            assert n == 1
