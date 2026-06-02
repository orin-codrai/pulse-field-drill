"""Tests for app/services/occurrences.py.

Pure unit-тесты, без БД. PlannedOperation создаётся в памяти (не commit'тится).
"""

from datetime import date, datetime, timezone

import pytest

from app.models import PlannedOperation
from app.services.occurrences import nth_occurrence, occurrences_in_window


def _plan(
    *,
    first_date: date,
    recurrence: str,
    completed_cycles: int = 0,
    total_cycles: int | None = None,
    status: str = "planned",
    archived_at: datetime | None = None,
) -> PlannedOperation:
    """In-memory план для тестов: ORM-объект без commit'a."""
    return PlannedOperation(
        workspace_id=1,
        kind="expense",
        amount_minor=100,
        currency="RUB",
        category_id=1,
        account_id=1,
        first_date=first_date,
        recurrence=recurrence,
        total_cycles=total_cycles,
        completed_cycles=completed_cycles,
        status=status,
        archived_at=archived_at,
    )


# ─── nth_occurrence ──────────────────────────────────────────────────────────


def test_nth_once_n0_returns_first_date():
    assert nth_occurrence(date(2026, 6, 15), "once", 0) == date(2026, 6, 15)


def test_nth_once_n1_returns_none():
    assert nth_occurrence(date(2026, 6, 15), "once", 1) is None


def test_nth_week_n0_and_n4():
    base = date(2026, 6, 1)
    assert nth_occurrence(base, "week", 0) == date(2026, 6, 1)
    assert nth_occurrence(base, "week", 4) == date(2026, 6, 29)


def test_nth_month_31_clamps_to_feb_28_then_restores_to_mar_31():
    """Главный edge case: relativedelta(months=N) даёт ОДИН шаг от base,
    а не накопительный clamp. Февраль клампится, март восстановлен."""
    base = date(2026, 1, 31)
    assert nth_occurrence(base, "month", 1) == date(2026, 2, 28)
    assert nth_occurrence(base, "month", 2) == date(2026, 3, 31)
    assert nth_occurrence(base, "month", 3) == date(2026, 4, 30)
    assert nth_occurrence(base, "month", 4) == date(2026, 5, 31)


def test_nth_year_leap_day_clamps_to_feb_28():
    assert nth_occurrence(date(2024, 2, 29), "year", 1) == date(2025, 2, 28)
    assert nth_occurrence(date(2024, 2, 29), "year", 4) == date(2028, 2, 29)


def test_nth_unknown_recurrence_raises():
    with pytest.raises(ValueError, match="unknown recurrence"):
        nth_occurrence(date(2026, 1, 1), "decade", 0)


# ─── occurrences_in_window ───────────────────────────────────────────────────


def test_window_status_paused_returns_empty():
    p = _plan(first_date=date(2026, 6, 1), recurrence="month", status="paused")
    assert occurrences_in_window(p, date(2026, 1, 1), date(2026, 12, 31)) == []


def test_window_status_done_returns_empty():
    p = _plan(first_date=date(2026, 6, 1), recurrence="month", status="done")
    assert occurrences_in_window(p, date(2026, 1, 1), date(2026, 12, 31)) == []


def test_window_archived_returns_empty():
    p = _plan(
        first_date=date(2026, 6, 1),
        recurrence="month",
        archived_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert occurrences_in_window(p, date(2026, 1, 1), date(2026, 12, 31)) == []


def test_window_inclusive_start_includes_today_scheduled():
    """MF8-3: план scheduled today, не confirmed, должен попасть в окно для
    forecast'a и /due. Иначе обязательство утром 1-го числа игнорируется."""
    p = _plan(first_date=date(2026, 6, 1), recurrence="month")
    occs = occurrences_in_window(
        p, date(2026, 6, 1), date(2026, 6, 30), inclusive_start=True
    )
    assert occs == [date(2026, 6, 1)]


def test_window_exclusive_start_skips_today_scheduled():
    p = _plan(first_date=date(2026, 6, 1), recurrence="month")
    occs = occurrences_in_window(
        p, date(2026, 6, 1), date(2026, 6, 30), inclusive_start=False
    )
    assert occs == []


def test_window_weekly_starts_from_completed_cycles_offset():
    """completed_cycles — источник истины «сколько уже подтверждено»;
    occurrences_in_window НЕ переотдаёт уже подтверждённые вхождения."""
    p = _plan(
        first_date=date(2026, 6, 1),  # понедельник
        recurrence="week",
        completed_cycles=2,
    )
    # Прошли 2 вхождения (1, 8 июня), следующее — 15.
    occs = occurrences_in_window(p, date(2026, 6, 10), date(2026, 6, 30))
    assert occs == [date(2026, 6, 15), date(2026, 6, 22), date(2026, 6, 29)]


def test_window_clipped_by_total_cycles():
    """План total=12, completed=10 → осталось ровно 2 вхождения."""
    p = _plan(
        first_date=date(2026, 1, 31),
        recurrence="month",
        completed_cycles=10,
        total_cycles=12,
    )
    occs = occurrences_in_window(p, date(2026, 1, 1), date(2027, 12, 31))
    assert len(occs) == 2
    # 11-е и 12-е вхождения = январь+10mo и +11mo.
    assert occs[0] == date(2026, 11, 30)  # 31jan + 10mo clamp на nov-30
    assert occs[1] == date(2026, 12, 31)  # 31jan + 11mo


def test_window_once_with_completed_returns_empty():
    p = _plan(first_date=date(2026, 6, 1), recurrence="once", completed_cycles=1)
    assert occurrences_in_window(p, date(2026, 1, 1), date(2026, 12, 31)) == []


def test_window_once_with_first_in_range_returns_first_date():
    p = _plan(first_date=date(2026, 6, 1), recurrence="once")
    occs = occurrences_in_window(p, date(2026, 1, 1), date(2026, 12, 31))
    assert occs == [date(2026, 6, 1)]


def test_window_monthly_clipped_by_end():
    """window_end отрезает будущие вхождения."""
    p = _plan(first_date=date(2026, 1, 1), recurrence="month")
    occs = occurrences_in_window(p, date(2026, 1, 1), date(2026, 3, 31))
    # 1jan + 0mo, +1mo, +2mo = 1jan, 1feb, 1mar; 1apr выпадает.
    assert occs == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
