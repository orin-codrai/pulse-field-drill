from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_workspace
from app.db.session import get_session
from app.models import Budget, Category, Transaction, Workspace
from app.services.resolvers import resolve_category
from app.schemas.budget import (
    BudgetCreate,
    BudgetOut,
    BudgetStatusItem,
    BudgetUpdate,
)

router = APIRouter(prefix="/budgets", tags=["budgets"])


async def _validate_category(
    session: AsyncSession, category_id: int, workspace_id: int
) -> None:
    """Mirror _validate_category_ref из transactions.py.
    Системная (workspace_id IS NULL) или принадлежащая workspace. Не archived.
    """
    cat = await resolve_category(session, category_id, workspace_id)
    if cat is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "category_id: not found"
        )
    if cat.archived_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "category_id: category is archived"
        )


def _map_integrity_error(e: IntegrityError) -> HTTPException:
    """Единый mapping для POST и PATCH. Применяется к partial unique
    `budgets_active_uq` (409) и CHECK `budgets_dates_chk` (422)."""
    msg = str(e.orig)
    if "budgets_active_uq" in msg:
        return HTTPException(
            status.HTTP_409_CONFLICT,
            "active budget for this category and period already exists",
        )
    if "budgets_dates_chk" in msg:
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "ends_on must be after starts_on",
        )
    return HTTPException(status.HTTP_400_BAD_REQUEST, "integrity error")


@router.get("", response_model=list[BudgetOut])
async def list_budgets(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> list[Budget]:
    stmt = select(Budget).where(Budget.workspace_id == ws.id).order_by(Budget.id)
    return list((await session.execute(stmt)).scalars().all())


@router.post("", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
async def create_budget(
    body: BudgetCreate,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> Budget:
    await _validate_category(session, body.category_id, ws.id)
    budget = Budget(workspace_id=ws.id, **body.model_dump(exclude_none=True))
    session.add(budget)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        mapped = _map_integrity_error(e)
        if mapped.status_code == status.HTTP_400_BAD_REQUEST:
            raise
        raise mapped from e
    await session.refresh(budget)
    return budget


@router.patch("/{budget_id}", response_model=BudgetOut)
async def update_budget(
    budget_id: int,
    body: BudgetUpdate,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> Budget:
    budget = await session.scalar(
        select(Budget).where(Budget.id == budget_id, Budget.workspace_id == ws.id)
    )
    if budget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "budget not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(budget, field, value)

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        mapped = _map_integrity_error(e)
        if mapped.status_code == status.HTTP_400_BAD_REQUEST:
            raise
        raise mapped from e
    await session.refresh(budget)
    return budget


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: int,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> None:
    budget = await session.scalar(
        select(Budget).where(Budget.id == budget_id, Budget.workspace_id == ws.id)
    )
    if budget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "budget not found")
    await session.delete(budget)
    await session.commit()


_PERIOD_INTERVAL = {"week": "7 days", "month": "1 month", "year": "1 year"}


@router.get("/status", response_model=list[BudgetStatusItem])
async def budgets_status(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> list[BudgetStatusItem]:
    """Status для активных бюджетов. Window:
    - ends_on IS NULL → [date_trunc(period, now()), +1 period). Текущий
      календарный период (week = ISO Monday-anchored, Postgres default).
      starts_on на open-ended бюджетах = metadata, spend до starts_on
      попадает в окно если оно >= date_trunc.
    - ends_on NOT NULL → [starts_on, ends_on]; expired (now()::date > ends_on)
      фильтруется из status'a (но остаётся в /api/budgets list).
    """
    stmt = select(Budget, Category.name).join(
        Category, Category.id == Budget.category_id
    ).where(
        Budget.workspace_id == ws.id,
        Budget.archived_at.is_(None),
    )
    rows = (await session.execute(stmt)).all()

    items: list[BudgetStatusItem] = []
    for budget, category_name in rows:
        # Период (window) + period_ends_on.
        if budget.ends_on is None:
            # Календарный period.
            period_start = await session.scalar(
                select(func.date_trunc(budget.period, func.now()))
            )
            interval = _PERIOD_INTERVAL[budget.period]
            period_end = await session.scalar(
                select(
                    func.date_trunc(budget.period, func.now())
                    + text(f"INTERVAL '{interval}'")
                )
            )
            period_ends_on = period_end.date() - timedelta(days=1)
        else:
            # Кампанийный бюджет.
            today = (await session.scalar(select(func.current_date())))
            if today > budget.ends_on:
                continue
            period_start = budget.starts_on
            period_end = budget.ends_on
            period_ends_on = budget.ends_on

        spent = await session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.workspace_id == ws.id,
                Transaction.kind == "expense",
                Transaction.category_id == budget.category_id,
                Transaction.occurred_at >= period_start,
                Transaction.occurred_at < period_end if budget.ends_on is None
                else Transaction.occurred_at <= period_end,
            )
        )
        spent_minor = int(spent)
        percent = (
            round(100.0 * spent_minor / budget.limit_minor, 2)
            if budget.limit_minor > 0
            else 0.0
        )
        items.append(
            BudgetStatusItem(
                budget_id=budget.id,
                category_name=category_name,
                period=budget.period,
                spent_minor=spent_minor,
                limit_minor=budget.limit_minor,
                percent=percent,
                period_ends_on=period_ends_on,
            )
        )
    return items
