"""Запланированные операции — CRUD + confirm + due.

Подтверждение плана идемпотентно (двойной tap не делает двойную транзакцию):
- pre-check existing tx по (planned_operation_id, occurrence_date) — даёт
  точный 409 в типовом случае;
- partial unique index `transactions_planned_uq` — страхует cross-process
  race; в except-блоке матчим по `constraint_name`, не по подстроке.

Грейс CONFIRM_FUTURE_GRACE_DAYS=7 разрешает «подтвердить заранее» для
weekly-планов и monthly с известной датой. За пределами grace — 409.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, current_workspace
from app.db.session import get_session
from app.models import PlannedOperation, Transaction, User, Workspace
from app.schemas.planned import (
    DuePlannedItem,
    PlannedOperationCreate,
    PlannedOperationOut,
    PlannedOperationUpdate,
)
from app.schemas.transaction import TransactionOut
from app.services.envelopes import skim_on_income
from app.services.occurrences import nth_occurrence
from app.services.resolvers import resolve_account, resolve_category

router = APIRouter(prefix="/planned", tags=["planned"])

CONFIRM_FUTURE_GRACE_DAYS = 7


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


async def _validate_account_ref(
    session: AsyncSession, account_id: int, workspace_id: int, field: str
) -> None:
    """C9-1 template — бит-в-бит как в transactions._validate_account_ref."""
    acc = await resolve_account(session, account_id, workspace_id)
    if acc is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field}: not found"
        )
    if acc.archived_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{field}: account is archived",
        )


async def _validate_category_for_planned(
    session: AsyncSession,
    category_id: int,
    workspace_id: int,
    plan_kind: str,
) -> None:
    """MF6: kind-matching. `transactions._validate_category_ref` оставлен
    либеральным (XOR-CHECK страхует tx); для плана — строго, потому что
    confirm создаёт tx без юзерского ввода kind."""
    cat = await resolve_category(session, category_id, workspace_id)
    if cat is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "category_id: not found"
        )
    if cat.archived_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "category_id: category is archived",
        )
    if cat.kind != "both" and cat.kind != plan_kind:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"category_id: kind={cat.kind} does not match plan kind={plan_kind}",
        )


# ─── CRUD ────────────────────────────────────────────────────────────────────


@router.post("", response_model=PlannedOperationOut, status_code=status.HTTP_201_CREATED)
async def create_planned(
    body: PlannedOperationCreate,
    ws: Workspace = Depends(current_workspace),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> PlannedOperation:
    await _validate_account_ref(session, body.account_id, ws.id, "account_id")
    await _validate_category_for_planned(
        session, body.category_id, ws.id, body.kind
    )
    op = PlannedOperation(
        workspace_id=ws.id, created_by_user_id=user.id, **body.model_dump()
    )
    session.add(op)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        msg = str(e.orig)
        if "planned_" in msg and "_chk" in msg:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "planned op violates a CHECK constraint",
            ) from e
        raise
    await session.refresh(op)
    return op


@router.get("", response_model=list[PlannedOperationOut])
async def list_planned(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
    status_filter: Literal["planned", "paused", "done"] | None = Query(
        default=None, alias="status"
    ),
    include_archived: bool = Query(default=False),
) -> list[PlannedOperation]:
    stmt = select(PlannedOperation).where(PlannedOperation.workspace_id == ws.id)
    if not include_archived:
        stmt = stmt.where(PlannedOperation.archived_at.is_(None))
    if status_filter is not None:
        stmt = stmt.where(PlannedOperation.status == status_filter)
    stmt = stmt.order_by(PlannedOperation.id)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/due", response_model=list[DuePlannedItem])
async def due_planned(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> list[DuePlannedItem]:
    """Первое неподтверждённое вхождение каждого активного плана со
    scheduled_date <= today. После confirm'a следующий вызов вернёт следующее
    вхождение (если ещё в прошлом/сегодня)."""
    today = _today_utc()
    plans = (await session.execute(
        select(PlannedOperation).where(
            PlannedOperation.workspace_id == ws.id,
            PlannedOperation.status == "planned",
            PlannedOperation.archived_at.is_(None),
        )
    )).scalars().all()

    items: list[DuePlannedItem] = []
    for p in plans:
        scheduled = nth_occurrence(p.first_date, p.recurrence, p.completed_cycles)
        if scheduled is None:
            continue
        if scheduled <= today:
            items.append(
                DuePlannedItem(
                    planned_operation_id=p.id,
                    scheduled_date=scheduled,
                    amount_minor=p.amount_minor,
                    kind=p.kind,
                    currency=p.currency,
                    category_id=p.category_id,
                    account_id=p.account_id,
                    note=p.note,
                )
            )
    items.sort(key=lambda x: x.scheduled_date)
    return items


@router.patch("/{op_id}", response_model=PlannedOperationOut)
async def update_planned(
    op_id: int,
    body: PlannedOperationUpdate,
    ws: Workspace = Depends(current_workspace),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> PlannedOperation:
    op = await session.scalar(
        select(PlannedOperation).where(
            PlannedOperation.id == op_id, PlannedOperation.workspace_id == ws.id
        )
    )
    if op is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "planned op not found")

    updates = body.model_dump(exclude_unset=True)

    # MF2: после первого confirm менять first_date / recurrence нельзя —
    # nth_occurrence пересчитался бы от нового базиса и сдвинул бы будущие
    # вхождения относительно уже подтверждённых tx.
    if "first_date" in updates and op.completed_cycles > 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "cannot change first_date after first confirm",
        )
    if "recurrence" in updates and op.completed_cycles > 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "cannot change recurrence after first confirm",
        )

    # MF8-4: PATCH с явным null в NOT NULL колонках — ловим до commit'a.
    # account_id всегда NOT NULL; category_id NOT NULL (MF10-1).
    if "account_id" in updates and updates["account_id"] is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "account_id cannot be null"
        )
    if "category_id" in updates and updates["category_id"] is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "category_id cannot be null"
        )

    if "account_id" in updates:
        await _validate_account_ref(session, updates["account_id"], ws.id, "account_id")
    if "category_id" in updates:
        await _validate_category_for_planned(
            session, updates["category_id"], ws.id, op.kind
        )

    for field, value in updates.items():
        setattr(op, field, value)
    try:
        await session.commit()
    except IntegrityError as e:
        # Defence-in-depth: пропущенный CHECK маппится в 422, не 500.
        await session.rollback()
        msg = str(e.orig)
        if "planned_" in msg and "_chk" in msg:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "planned op violates a CHECK constraint",
            ) from e
        raise
    await session.refresh(op)
    return op


@router.delete("/{op_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_planned(
    op_id: int,
    ws: Workspace = Depends(current_workspace),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """FK transactions.planned_operation_id ondelete='RESTRICT' (после
    миграции 0005 шаг 9, C13-1): DELETE plan с confirmed tx → IntegrityError
    → 409 (MF12-1). Симметрично с MF11-2 (DELETE tx из плана → 409).
    Юзер архивирует, не удаляет — consistency с history-immutable."""
    op = await session.scalar(
        select(PlannedOperation).where(
            PlannedOperation.id == op_id, PlannedOperation.workspace_id == ws.id
        )
    )
    if op is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "planned op not found")
    try:
        await session.delete(op)
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        msg = str(e.orig)
        if "transactions_planned_operation_id_fkey" in msg:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "cannot delete plan with confirmed transactions; "
                "archive plan or delete linked transactions first",
            ) from e
        raise


# ─── Confirm ─────────────────────────────────────────────────────────────────


@router.post(
    "/{op_id}/confirm",
    response_model=None,  # response сериализуется через TransactionOut явно ниже
    status_code=status.HTTP_201_CREATED,
)
async def confirm_planned(
    op_id: int,
    ws: Workspace = Depends(current_workspace),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    op = await session.scalar(
        select(PlannedOperation).where(
            PlannedOperation.id == op_id, PlannedOperation.workspace_id == ws.id
        )
    )
    if op is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "planned op not found")
    if op.status != "planned":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"cannot confirm: status={op.status}"
        )
    if op.archived_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "cannot confirm: archived"
        )

    scheduled = nth_occurrence(op.first_date, op.recurrence, op.completed_cycles)
    if scheduled is None:
        # once с completed>=1 должно было быть status=done; defence-in-depth.
        raise HTTPException(status.HTTP_409_CONFLICT, "no more occurrences")

    today = _today_utc()
    if scheduled > today + timedelta(days=CONFIRM_FUTURE_GRACE_DAYS):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"cannot confirm before scheduled date "
            f"(scheduled={scheduled}, today={today}, "
            f"grace={CONFIRM_FUTURE_GRACE_DAYS}d)",
        )

    # MF9: pre-check existing — точный 409 в обычном двойном-tap. Race между
    # connection'ами всё равно покрывается unique partial index в except.
    existing = await session.scalar(
        select(Transaction.id).where(
            Transaction.planned_operation_id == op.id,
            Transaction.occurrence_date == scheduled,
        )
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "occurrence already confirmed"
        )

    # Tx — атомарно с инкрементом completed_cycles.
    tx_payload: dict = {
        "workspace_id": ws.id,
        # P7: actor = тот, кто confirm нажал (не plan creator).
        "created_by_user_id": user.id,
        "kind": op.kind,
        "amount_minor": op.amount_minor,
        "currency": op.currency,  # MF8: пробрасываем из плана (защита от drift).
        "category_id": op.category_id,
        "planned_operation_id": op.id,
        "occurrence_date": scheduled,
    }
    if op.kind == "expense":
        tx_payload["from_account_id"] = op.account_id
    else:
        tx_payload["to_account_id"] = op.account_id

    tx = Transaction(**tx_payload)
    session.add(tx)

    op.completed_cycles += 1
    if op.total_cycles is not None and op.completed_cycles >= op.total_cycles:
        op.status = "done"
    elif op.recurrence == "once":
        op.status = "done"

    # Auto-skim для plan-подтверждённого income — единый путь с прямым POST tx
    # (ADR-0007). adjustment kind у плана невозможен (Literal income|expense
    # в PlannedOperationCreate).
    if op.kind == "income":
        await skim_on_income(session, tx, actor_user_id=user.id)

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        # MF9: asyncpg прокидывает constraint_name через diag — точнее, чем
        # `in str(e.orig)`. Race между двумя connection'ами на одно вхождение.
        constraint = getattr(getattr(e.orig, "diag", None), "constraint_name", None) \
            or getattr(e.orig, "constraint_name", None)
        if constraint == "transactions_planned_uq":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "occurrence already confirmed (race)"
            ) from e
        # MF3 pass 11 / C12-2: entries CHECK/FK → 422, не 500.
        msg = str(e.orig)
        if "envelope_entries_" in msg and ("_chk" in msg or "_fkey" in msg):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "envelope entry constraint violated",
            ) from e
        raise

    await session.refresh(tx)
    return TransactionOut.model_validate(tx)
