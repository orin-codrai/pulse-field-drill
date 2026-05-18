from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.session import get_session
from app.models import Category, Transaction, User
from app.schemas.report import CalendarItem, MonthReport

router = APIRouter(prefix="/reports", tags=["reports"])


def _month_window(year: int | None, month: int | None) -> tuple[datetime, datetime]:
    """Возвращает [start, end) текущего или указанного месяца, UTC-aware."""
    now = datetime.now(timezone.utc)
    y = year if year is not None else now.year
    m = month if month is not None else now.month
    if not (1 <= m <= 12):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "month must be 1..12")
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = (
        datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        if m == 12
        else datetime(y, m + 1, 1, tzinfo=timezone.utc)
    )
    return start, end


@router.get("/month", response_model=MonthReport)
async def month_report(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
) -> MonthReport:
    """Месячный отчёт. По умолчанию текущий месяц (UTC).
    Empty month: by_category всегда {} (dict), не отсутствующий ключ
    (см. open Q #4 в plan v2).
    """
    start, end = _month_window(year, month)

    # Сразу две агрегации: by_category (только expense) + by_kind.
    by_cat_rows = (
        await session.execute(
            select(Category.name, func.sum(Transaction.amount_minor))
            .join(Category, Category.id == Transaction.category_id)
            .where(
                Transaction.user_id == user.id,
                Transaction.kind == "expense",
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
            )
            .group_by(Category.name)
        )
    ).all()
    by_category = {name: int(total) for name, total in by_cat_rows}

    by_kind_rows = (
        await session.execute(
            select(Transaction.kind, func.sum(Transaction.amount_minor))
            .where(
                Transaction.user_id == user.id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
            )
            .group_by(Transaction.kind)
        )
    ).all()
    by_kind = {kind: int(total) for kind, total in by_kind_rows}

    return MonthReport(
        by_category=by_category,
        by_kind=by_kind,
        total_expense=by_kind.get("expense", 0),
        total_income=by_kind.get("income", 0),
    )


@router.get("/calendar", response_model=list[CalendarItem])
async def calendar_report(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> list[CalendarItem]:
    """Календарь expense/income по дням. По умолчанию текущий месяц."""
    if date_from is None or date_to is None:
        now = datetime.now(timezone.utc)
        m_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        m_end = (
            datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
            if now.month == 12
            else datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        )
        start_dt = m_start if date_from is None else datetime(
            date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc
        )
        end_dt = m_end if date_to is None else datetime(
            date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc
        )
    else:
        start_dt = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
        end_dt = datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc)

    day = func.date_trunc("day", Transaction.occurred_at)
    rows = (
        await session.execute(
            select(
                day.label("day"),
                Transaction.kind,
                func.sum(Transaction.amount_minor),
            )
            .where(
                Transaction.user_id == user.id,
                Transaction.occurred_at >= start_dt,
                Transaction.occurred_at < end_dt,
                Transaction.kind.in_(("expense", "income")),
            )
            .group_by(day, Transaction.kind)
            .order_by(day)
        )
    ).all()

    agg: dict[date, dict[str, int]] = {}
    for d, kind, total in rows:
        key = d.date() if hasattr(d, "date") else d
        agg.setdefault(key, {"expense": 0, "income": 0})[kind] = int(total)

    return [
        CalendarItem(date=d, expense=v["expense"], income=v["income"])
        for d, v in sorted(agg.items())
    ]
