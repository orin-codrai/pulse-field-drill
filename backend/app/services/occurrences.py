"""Чистые (без БД) функции вычисления вхождений запланированной операции.

Контракт `nth_occurrence` критичен: `relativedelta(months=N)` даёт **один шаг
от base date**, не накопительный clamp:

    date(2026,1,31) + relativedelta(months=1) = 2026-02-28   (clamp end-of-month)
    date(2026,1,31) + relativedelta(months=2) = 2026-03-31   (восстановлено)

Это означает, что n-е вхождение monthly-плана, созданного 31-го числа, в
феврале клампится на 28/29, но в марте/апреле/мае ведёт себя как ожидается.
Если бы вычисление накапливалось (`first + 1mo + 1mo + ...`), оно бы залипло
на 28-м числе после первого февральского clamp'a.

Развёртка окна — событийная: `inclusive_start=True` по умолчанию (включает
вхождение в день `window_start`), потому что (1) `/due` должен показывать
план scheduled today; (2) forecast должен учитывать сегодняшнее обязательство,
иначе projected_balance ложно высок (MF8-3). Exclusive вариант оставлен для
специальных запросов «только будущее, не сегодня».
"""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from app.models import PlannedOperation


def nth_occurrence(first_date: date, recurrence: str, n: int) -> date | None:
    """n-е вхождение (0-indexed) плана. None для once при n >= 1."""
    if recurrence == "once":
        return first_date if n == 0 else None
    if recurrence == "week":
        return first_date + timedelta(weeks=n)
    if recurrence == "month":
        return first_date + relativedelta(months=n)
    if recurrence == "year":
        return first_date + relativedelta(years=n)
    raise ValueError(f"unknown recurrence: {recurrence}")


def occurrences_in_window(
    plan: PlannedOperation,
    window_start: date,
    window_end: date,
    *,
    inclusive_start: bool = True,
) -> list[date]:
    """Вхождения плана в окне, начиная со СЛЕДУЮЩЕГО неподтверждённого
    (n = `plan.completed_cycles` — единственный источник истины о прогрессе).

    `inclusive_start=True` (default): окно `[window_start, window_end]`.
    `inclusive_start=False`: окно `(window_start, window_end]`.

    Возвращает пустой список для status != 'planned' или archived плана —
    paused/done не дают новых вхождений, archived вообще не считается.
    """
    if plan.status != "planned" or plan.archived_at is not None:
        return []
    result: list[date] = []
    n = plan.completed_cycles
    while True:
        if plan.total_cycles is not None and n >= plan.total_cycles:
            break
        occ = nth_occurrence(plan.first_date, plan.recurrence, n)
        if occ is None:
            break  # once и n >= 1
        if occ > window_end:
            break
        in_window = occ >= window_start if inclusive_start else occ > window_start
        if in_window:
            result.append(occ)
        n += 1
    return result
