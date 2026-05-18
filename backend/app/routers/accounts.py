from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.session import get_session
from app.models import Account, User
from app.schemas.account import (
    AccountBalanceOut,
    AccountCreate,
    AccountOut,
    AccountUpdate,
)
from app.services.balances import all_balances

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
async def list_accounts(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Account]:
    stmt = select(Account).where(Account.user_id == user.id).order_by(Account.id)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/balances", response_model=list[AccountBalanceOut])
async def get_balances(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Derived балансы всех accounts юзера, один SQL. См. ADR-0004."""
    return await all_balances(session, user.id)


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: AccountCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Account:
    acc = Account(user_id=user.id, **body.model_dump())
    session.add(acc)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "accounts_user_name_uq" in str(e.orig):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "account with this name already exists",
            ) from e
        raise
    await session.refresh(acc)
    return acc


@router.patch("/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: int,
    body: AccountUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Account:
    # Filter by user_id в самом SELECT, не post-fetch. Mismatch → 404,
    # не 403 — чтобы не утекало существование чужих ID.
    stmt = select(Account).where(
        Account.id == account_id, Account.user_id == user.id
    )
    acc = await session.scalar(stmt)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(acc, field, value)

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "accounts_user_name_uq" in str(e.orig):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "account with this name already exists",
            ) from e
        raise
    await session.refresh(acc)
    return acc
