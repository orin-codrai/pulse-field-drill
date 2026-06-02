"""Derived balances. Event-sourced — баланс счёта вычисляется из транзакций
on-read, не хранится. См. ADR-0004.

После XOR-CHECK на `adjustment` каждая adjustment-строка имеет ровно один
non-null FK; in/out join-семантика безопасна.
"""

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Transaction


@dataclass(slots=True)
class AccountBalance:
    account_id: int
    name: str
    type: str
    balance_minor: int


async def account_balance(session: AsyncSession, account_id: int) -> int:
    """Баланс одного счёта через ту же CASE/JOIN агрегацию, что и all_balances.

    Используется goals progress (linked_account_id) — там нужен один баланс,
    а не все. Дублирует SQL форму из all_balances, но без user-filter (caller
    отвечает за проверку ownership).
    """
    in_amount = case(
        (
            (Transaction.to_account_id == account_id)
            & Transaction.kind.in_(("income", "transfer", "adjustment")),
            Transaction.amount_minor,
        ),
        else_=0,
    )
    out_amount = case(
        (
            (Transaction.from_account_id == account_id)
            & Transaction.kind.in_(("expense", "transfer", "adjustment")),
            Transaction.amount_minor,
        ),
        else_=0,
    )
    stmt = (
        select(
            Account.initial_balance_minor
            + func.coalesce(func.sum(in_amount), 0)
            - func.coalesce(func.sum(out_amount), 0)
        )
        .select_from(Account)
        .outerjoin(
            Transaction,
            (Transaction.from_account_id == Account.id)
            | (Transaction.to_account_id == Account.id),
        )
        .where(Account.id == account_id)
        .group_by(Account.initial_balance_minor)
    )
    result = await session.scalar(stmt)
    return int(result) if result is not None else 0


async def all_balances(session: AsyncSession, workspace_id: int) -> list[AccountBalance]:
    """Балансы всех accounts workspace. Один SQL, без N+1.

    Балансовая формула:
        balance = initial_balance_minor
                + Σ(amount when to_account=a AND kind ∈ {income,transfer,adjustment})
                − Σ(amount when from_account=a AND kind ∈ {expense,transfer,adjustment})
    """
    in_amount = case(
        (
            (Transaction.to_account_id == Account.id)
            & Transaction.kind.in_(("income", "transfer", "adjustment")),
            Transaction.amount_minor,
        ),
        else_=0,
    )
    out_amount = case(
        (
            (Transaction.from_account_id == Account.id)
            & Transaction.kind.in_(("expense", "transfer", "adjustment")),
            Transaction.amount_minor,
        ),
        else_=0,
    )

    stmt = (
        select(
            Account.id,
            Account.name,
            Account.type,
            (
                Account.initial_balance_minor
                + func.coalesce(func.sum(in_amount), 0)
                - func.coalesce(func.sum(out_amount), 0)
            ).label("balance_minor"),
        )
        .outerjoin(
            Transaction,
            (Transaction.from_account_id == Account.id)
            | (Transaction.to_account_id == Account.id),
        )
        .where(Account.workspace_id == workspace_id)
        .group_by(Account.id, Account.name, Account.type, Account.initial_balance_minor)
        .order_by(Account.id)
    )

    rows = (await session.execute(stmt)).all()
    return [
        AccountBalance(
            account_id=row.id,
            name=row.name,
            type=row.type,
            balance_minor=row.balance_minor,
        )
        for row in rows
    ]
