from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, current_workspace
from app.db.session import get_session
from app.models import Transaction, User, Workspace
from app.services.envelopes import skim_on_income
from app.services.resolvers import resolve_account, resolve_category
from app.schemas.transaction import (
    TransactionCreate,
    TransactionKind,
    TransactionOut,
    TransactionUpdate,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


async def _validate_account_ref(
    session: AsyncSession, account_id: int, workspace_id: int, field: str
) -> None:
    """Account FK должен указывать на active (не archived) account workspace."""
    acc = await resolve_account(session, account_id, workspace_id)
    if acc is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field}: not found")
    if acc.archived_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field}: account is archived"
        )


async def _validate_category_ref(
    session: AsyncSession, category_id: int, workspace_id: int
) -> None:
    """Category FK: системная (workspace_id IS NULL) или принадлежащая workspace."""
    cat = await resolve_category(session, category_id, workspace_id)
    if cat is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "category_id: not found"
        )
    if cat.archived_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "category_id: category is archived"
        )


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    body: TransactionCreate,
    ws: Workspace = Depends(current_workspace),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Transaction:
    # Application-level FK ownership / archived-checks — отвечаем 422 с
    # понятным detail вместо неинформативного 500 от DB IntegrityError.
    if body.from_account_id is not None:
        await _validate_account_ref(
            session, body.from_account_id, ws.id, "from_account_id"
        )
    if body.to_account_id is not None:
        await _validate_account_ref(
            session, body.to_account_id, ws.id, "to_account_id"
        )
    if body.category_id is not None:
        await _validate_category_ref(session, body.category_id, ws.id)

    tx = Transaction(workspace_id=ws.id, **body.model_dump(exclude_none=True))
    session.add(tx)

    # Auto-skim для активных конвертов при подтверждении дохода. В той же
    # session: либо tx+entries уходят одной atomic-транзакцией, либо ни one
    # (commit ниже). adjustment с to_account_id растит баланс, но НЕ доход
    # (MF4 ADR-0007) — skim_on_income raise'нет на любом non-income.
    if body.kind == "income":
        await skim_on_income(session, tx, actor_user_id=user.id)

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        msg = str(e.orig)
        # CHECK violations (XOR, currency, amount, kind enum) — все 422.
        if "transactions_kind_fields_chk" in msg:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "kind/account/category combination not valid; see "
                "transactions_kind_fields_chk in schema",
            ) from e
        if "transactions_" in msg and "_chk" in msg:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "transaction violates a CHECK constraint",
            ) from e
        # MF3 pass 11 / C12-2: CHECK/FK violations на entries → 422, не 500.
        # Любая новая constraint на envelope_entries (включая будущие _uq)
        # требует расширения этого matcher'a — сейчас покрываем _chk/_fkey.
        if "envelope_entries_" in msg and ("_chk" in msg or "_fkey" in msg):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "envelope entry constraint violated",
            ) from e
        raise
    await session.refresh(tx)
    return tx


@router.get("", response_model=list[TransactionOut])
async def list_transactions(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
    kind: TransactionKind | None = Query(default=None),
    account_id: int | None = Query(default=None),
    category_id: int | None = Query(default=None),
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[Transaction]:
    """Список транзакций workspace, новые сверху. Cursor pagination — позже."""
    stmt = select(Transaction).where(Transaction.workspace_id == ws.id)
    if kind is not None:
        stmt = stmt.where(Transaction.kind == kind)
    if account_id is not None:
        stmt = stmt.where(
            or_(
                Transaction.from_account_id == account_id,
                Transaction.to_account_id == account_id,
            )
        )
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id == category_id)
    if date_from is not None:
        stmt = stmt.where(Transaction.occurred_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Transaction.occurred_at < date_to)
    stmt = stmt.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()).limit(
        limit
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get("/{tx_id}", response_model=TransactionOut)
async def get_transaction(
    tx_id: int,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> Transaction:
    tx = await session.scalar(
        select(Transaction).where(
            Transaction.id == tx_id, Transaction.workspace_id == ws.id
        )
    )
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")
    return tx


@router.patch("/{tx_id}", response_model=TransactionOut)
async def update_transaction(
    tx_id: int,
    body: TransactionUpdate,
    ws: Workspace = Depends(current_workspace),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Transaction:
    """Whitelist: только note и occurred_at. Сумма/тип/FK иммутабельны —
    чтобы поменять, удалить и создать заново. Сохраняет историю чистой."""
    tx = await session.scalar(
        select(Transaction).where(
            Transaction.id == tx_id, Transaction.workspace_id == ws.id
        )
    )
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(tx, field, value)
    await session.commit()
    await session.refresh(tx)
    return tx


@router.delete("/{tx_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    tx_id: int,
    ws: Workspace = Depends(current_workspace),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    tx = await session.scalar(
        select(Transaction).where(
            Transaction.id == tx_id, Transaction.workspace_id == ws.id
        )
    )
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")
    # MF11-2: DELETE planned-tx → zombie occurrence (CASCADE снимает skim
    # entries и balance падает, но planned_operations.completed_cycles не
    # откатывается → план не вернётся в /due, повторный confirm невозможен).
    # Юзер должен архивировать или удалить план — guard в delete_planned
    # симметрично (MF12-1: tx.planned_operation_id_fkey ondelete=RESTRICT).
    if tx.planned_operation_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "cannot delete transaction linked to a plan; "
            "delete the plan first (or archive it)",
        )
    await session.delete(tx)
    await session.commit()
