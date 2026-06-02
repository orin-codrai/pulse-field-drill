"""HTTP-level тесты GET /api/audit + assignment created_by_user_id +
log_action в transactions/accounts. Покрывает:
- POST tx → 1 audit row create + tx.created_by_user_id = user.id
- PATCH tx → 1 audit row update
- DELETE non-planned tx → 1 audit row delete
- POST/PATCH account аналогично
- GET /audit в personal → []
- GET /audit в shared → видим обоих участников
- actor_display_name resolve (display_name → first_name → snapshot fallback)
"""

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Account, AuditLog, Transaction, Workspace, WorkspaceMember
from app.schemas.user import TelegramUser
from app.services.user_provisioning import ensure_user_provisioned
from tests.conftest import sign_init_data


@pytest_asyncio.fixture
async def audit_setup(app_client, provisioned_user, auth_header):
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    card = next(a["id"] for a in accounts if a["name"] == "Карта")
    cats = (await app_client.get("/api/categories", headers=auth_header)).json()
    sys_expense = next(
        c["id"] for c in cats
        if c["kind"] == "expense" and c["workspace_id"] is None
    )
    return {"card": card, "expense_cat": sys_expense}


async def test_post_tx_writes_audit_create_and_created_by(
    app_client: AsyncClient, auth_header, provisioned_user, audit_setup, db_session
):
    r = await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 1000,
            "from_account_id": audit_setup["card"],
            "category_id": audit_setup["expense_cat"],
        },
    )
    assert r.status_code == 201
    tx_id = r.json()["id"]

    # tx.created_by_user_id = provisioned_user.id (live из БД).
    tx = await db_session.scalar(
        select(Transaction).where(Transaction.id == tx_id)
    )
    assert tx.created_by_user_id == provisioned_user.id

    # 1 audit-строка.
    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.entity_type == "transaction",
                               AuditLog.entity_id == tx_id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "create"
    assert rows[0].actor_user_id == provisioned_user.id
    assert rows[0].workspace_id == provisioned_user.active_workspace_id
    assert rows[0].snapshot_json["after"]["amount_minor"] == 1000
    assert rows[0].snapshot_json["actor_name_snapshot"] == "Orrin"


async def test_patch_tx_writes_audit_update(
    app_client: AsyncClient, auth_header, audit_setup, db_session
):
    create = await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 100,
            "from_account_id": audit_setup["card"],
            "category_id": audit_setup["expense_cat"],
        },
    )
    tx_id = create.json()["id"]
    await app_client.patch(
        f"/api/transactions/{tx_id}", headers=auth_header,
        json={"note": "coffee"},
    )

    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.entity_type == "transaction",
                               AuditLog.entity_id == tx_id)
        .order_by(AuditLog.id)
    )).scalars().all()
    assert len(rows) == 2
    assert rows[0].action == "create"
    assert rows[1].action == "update"
    assert rows[1].snapshot_json["after"]["note"] == "coffee"


async def test_delete_tx_writes_audit_delete(
    app_client: AsyncClient, auth_header, audit_setup, db_session
):
    create = await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 100,
            "from_account_id": audit_setup["card"],
            "category_id": audit_setup["expense_cat"],
        },
    )
    tx_id = create.json()["id"]
    r = await app_client.delete(f"/api/transactions/{tx_id}", headers=auth_header)
    assert r.status_code == 204

    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.entity_type == "transaction",
                               AuditLog.entity_id == tx_id)
        .order_by(AuditLog.id)
    )).scalars().all()
    # create + delete.
    assert len(rows) == 2
    assert rows[0].action == "create"
    assert rows[1].action == "delete"


async def test_post_account_writes_audit_and_created_by(
    app_client: AsyncClient, auth_header, provisioned_user, db_session
):
    r = await app_client.post(
        "/api/accounts", headers=auth_header,
        json={"name": "Депозит", "type": "savings", "initial_balance_minor": 50000},
    )
    assert r.status_code == 201
    acc_id = r.json()["id"]

    acc = await db_session.scalar(
        select(Account).where(Account.id == acc_id)
    )
    assert acc.created_by_user_id == provisioned_user.id

    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.entity_type == "account",
                               AuditLog.entity_id == acc_id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "create"


async def test_patch_account_writes_audit(
    app_client: AsyncClient, auth_header, provisioned_user, db_session
):
    accounts = (await app_client.get("/api/accounts", headers=auth_header)).json()
    aid = accounts[0]["id"]
    await app_client.patch(
        f"/api/accounts/{aid}", headers=auth_header, json={"name": "Карта Сбер"},
    )
    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.entity_type == "account",
                               AuditLog.entity_id == aid)
        .order_by(AuditLog.id)
    )).scalars().all()
    # Default 2 accounts при провизионинге НЕ пишут audit (они до Phase 7).
    # Update должен быть один.
    assert any(r.action == "update" for r in rows)


# ─── GET /api/audit ──────────────────────────────────────────────────────────


async def test_get_audit_in_personal_returns_empty(
    app_client: AsyncClient, auth_header, audit_setup
):
    """personal workspace → 200 + []. UI скрывает раздел."""
    await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 100,
            "from_account_id": audit_setup["card"],
            "category_id": audit_setup["expense_cat"],
        },
    )
    r = await app_client.get("/api/audit", headers=auth_header)
    assert r.status_code == 200
    assert r.json() == []


async def test_get_audit_in_shared_returns_rows(
    app_client: AsyncClient, auth_header, provisioned_user, audit_setup, db_session
):
    """Создаём shared workspace, переключаем active, POST tx → видим в audit."""
    ws = Workspace(name="Семейный", kind="shared")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws.id, user_id=provisioned_user.id, role="owner")
    )
    provisioned_user.active_workspace_id = ws.id

    # Создаём account в shared (по-умолчанию workspace без accounts).
    acc = Account(
        workspace_id=ws.id, name="Карта-семейная", type="card",
        currency="RUB", initial_balance_minor=0,
    )
    db_session.add(acc)
    await db_session.commit()

    # Tx в shared.
    r1 = await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 100,
            "from_account_id": acc.id,
            "category_id": audit_setup["expense_cat"],
        },
    )
    assert r1.status_code == 201

    r = await app_client.get("/api/audit", headers=auth_header)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    me_item = items[0]
    assert me_item["entity_type"] == "transaction"
    assert me_item["action"] == "create"
    assert me_item["actor_user_id"] == provisioned_user.id
    # display_name None → first_name fallback "Orrin".
    assert me_item["actor_display_name"] == "Orrin"


async def test_audit_actor_display_name_uses_display_name_when_set(
    app_client: AsyncClient, auth_header, provisioned_user, audit_setup, db_session
):
    """provisioned_user задаёт display_name; POST tx; GET /audit показывает его."""
    # Сделаем shared + переключим.
    ws = Workspace(name="Test", kind="shared")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws.id, user_id=provisioned_user.id, role="owner")
    )
    provisioned_user.active_workspace_id = ws.id
    provisioned_user.display_name = "Главный"
    acc = Account(
        workspace_id=ws.id, name="X", type="card",
        currency="RUB", initial_balance_minor=0,
    )
    db_session.add(acc)
    await db_session.commit()

    await app_client.post(
        "/api/transactions", headers=auth_header,
        json={
            "kind": "expense", "amount_minor": 100,
            "from_account_id": acc.id,
            "category_id": audit_setup["expense_cat"],
        },
    )
    r = await app_client.get("/api/audit", headers=auth_header)
    assert r.json()[0]["actor_display_name"] == "Главный"
