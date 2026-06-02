from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_workspace
from app.db.session import get_session
from app.models import Account, Category, Transaction, Workspace
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
    acc = await session.scalar(
        select(Account).where(
            Account.id == account_id, Account.workspace_id == workspace_id
        )
    )
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
    cat = await session.scalar(
        select(Category).where(
            Category.id == category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )
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
    session: AsyncSession = Depends(get_session),
) -> None:
    tx = await session.scalar(
        select(Transaction).where(
            Transaction.id == tx_id, Transaction.workspace_id == ws.id
        )
    )
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")
    await session.delete(tx)
    await session.commit()
