"""Прогноз баланса на горизонте.

Формула (ADR-0008, v1.1):

    available_now       = Σ балансы счетов workspace                   (event-sourced)
    reserved            = Σ reserved активных конвертов                (Σ entries)
    planned_income      = Σ planned(kind=income,  status='planned'),
                            вхождения в [today, horizon]
    planned_expense     = Σ planned(kind=expense, status='planned'),
                            вхождения в [today, horizon]
    planned_skim        = Σ per occurrence per envelope:
                            floor(plan.amount_minor * envelope.percent / 100)
                          (только income-плана × active envelope.percent NOT NULL)
    projected_balance   = available_now + planned_income − planned_expense
    projected_available = projected_balance − reserved − planned_skim

Окно `[today, horizon]` (inclusive с обоих концов через
`occurrences_in_window(inclusive_start=True)`) — критично, иначе сегодняшнее
обязательство не отражено ни в available_now (tx ещё нет), ни в planned_*
→ projected_balance ложно высок и юзер пере-тратит (MF8-3).

`planned_skim` зеркалит `skim_on_income` (ADR-0007): per-occurrence per-envelope
floor — Σ скимов ≤ planned_income. Чистый доп-вычет, не рекурсия: planned_income
остаётся «грязным» (доход на счёт), planned_skim переносит долю в reserved
концептуально → projected_available «после скима».

Просроченные циклы (overdue, scheduled < today, не confirmed) в planned_*
НЕ считаются — по дизайну (C9-3). Юзер catch-up'ит через `/api/planned/due`.

MAX_HORIZON_MONTHS=13 clamp — защита от раздувания weekly без total_cycles.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timezone

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Envelope, EnvelopeEntry, PlannedOperation
from app.services.balances import all_balances
from app.services.occurrences import occurrences_in_window

MAX_HORIZON_MONTHS = 13


def today_utc() -> date:
    """Единый источник 'сегодня' для всех прогнозирующих сервисов.
    Goals.py использует тот же datetime.now(timezone.utc).date() — после
    retire goals.py (Phase 6) этот хелпер останется единственной точкой."""
    return datetime.now(timezone.utc).date()


def end_of_current_month() -> date:
    t = today_utc()
    return date(t.year, t.month, monthrange(t.year, t.month)[1])


@dataclass(slots=True)
class Forecast:
    available_now: int
    reserved: int
    planned_income: int
    planned_expense: int
    planned_skim: int
    projected_balance: int
    projected_available: int
    horizon: date


async def compute_forecast(
    session: AsyncSession,
    workspace_id: int,
    horizon: date | None = None,
) -> Forecast:
    today = today_utc()
    h = horizon or end_of_current_month()
    # Clamp до 13 месяцев — раньше всего, чтобы occurrences_in_window
    # не раздувался на бесконечном weekly. PIN-A: тихий clamp, не 422.
    max_h = today + relativedelta(months=MAX_HORIZON_MONTHS)
    if h > max_h:
        h = max_h

    balances = await all_balances(session, workspace_id)
    available_now = sum(b.balance_minor for b in balances)

    # Phase 6: Σ entries активных конвертов (query-filter B2 — архивные
    # исключены через WHERE Envelope.archived_at IS NULL, что освобождает
    # резерв без правки entries-истории; un-archive восстанавливает).
    reserved_raw = await session.scalar(
        select(func.coalesce(func.sum(EnvelopeEntry.amount_minor), 0))
        .join(Envelope, Envelope.id == EnvelopeEntry.envelope_id)
        .where(
            Envelope.workspace_id == workspace_id,
            Envelope.archived_at.is_(None),
        )
    )
    reserved = int(reserved_raw)

    # Active envelopes с percent (NOT NULL) — те же, что попадут в skim_on_income
    # при confirm каждого income-вхождения. Запрос один раз перед циклом.
    envelope_pcts: list[int] = list((await session.execute(
        select(Envelope.percent).where(
            Envelope.workspace_id == workspace_id,
            Envelope.archived_at.is_(None),
            Envelope.percent.is_not(None),
        )
    )).scalars().all())

    planned_income = 0
    planned_expense = 0
    planned_skim = 0
    if h >= today:
        plans = (await session.execute(
            select(PlannedOperation).where(
                PlannedOperation.workspace_id == workspace_id,
                PlannedOperation.status == "planned",
                PlannedOperation.archived_at.is_(None),
            )
        )).scalars().all()
        for plan in plans:
            # MF8-3: inclusive_start=True — план scheduled today попадает
            # в planned_*. Иначе сегодняшнее обязательство не отражено и
            # projected_balance ложно высок.
            occs = occurrences_in_window(plan, today, h)
            n = len(occs)
            total = plan.amount_minor * n
            if plan.kind == "income":
                planned_income += total
                # Зеркалит skim_on_income: per-envelope floor per occurrence.
                # Σ floor никогда не превысит сам amount → planned_skim ≤ total.
                per_occ_skim = sum(
                    (plan.amount_minor * pct) // 100 for pct in envelope_pcts
                )
                planned_skim += per_occ_skim * n
            else:
                planned_expense += total

    projected_balance = available_now + planned_income - planned_expense
    projected_available = projected_balance - reserved - planned_skim
    return Forecast(
        available_now=available_now,
        reserved=reserved,
        planned_income=planned_income,
        planned_expense=planned_expense,
        planned_skim=planned_skim,
        projected_balance=projected_balance,
        projected_available=projected_available,
        horizon=h,
    )
