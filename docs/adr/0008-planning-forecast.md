# ADR 0008 — Модуль планирования и прогноз баланса

- **Status:** Proposed (ждёт plan-reviewer)
- **Date:** 2026-05-28
- **Deciders:** @orrin

## Контекст

Главный dogfood-вывод: планирование важнее аналитики. Нужно вносить будущие траты/доходы
по датам с периодичностью и видеть прогноз «к концу месяца останется N». Аналитика
(Phase 3 старого плана) — в backlog (в списке транзакций всё видно).

## Решение

**Новая сущность** `planned_operations` (не переиспользуем budgets/goals — другая
семантика: budgets = ретро-лимит, goals→envelopes = резерв; план = будущая операция):

```
planned_operations(
  id, workspace_id,
  kind('income'|'expense'),
  amount_minor > 0,
  category_id?,                      -- FK, cross-workspace guard
  account_id,                        -- источник (expense) / назначение (income)
  first_date,
  recurrence('once'|'week'|'month'|'year'),
  total_cycles?,                     -- NULL = бесконечно (для recurring)
  completed_cycles DEFAULT 0,
  status('planned'|'paused'|'done'),
  note?, created_by_user_id, created_at, archived_at?
)
```

**Подтверждение (architecture #4 фидбека):** наступила дата → UI показывает в списке
«к подтверждению» → `POST /api/planned/{id}/confirm` создаёт реальную транзакцию +
`completed_cycles += 1` (инкремент счётчика **и есть** «сдвиг следующего вхождения»);
циклы исчерпаны → `status='done'`. **Идемпотентность (must-fix):** на `transactions`
добавляем `planned_operation_id bigint NULL FK→planned_operations` + `occurrence_date
date NULL`, unique `(planned_operation_id, occurrence_date)`. **`occurrence_date` =
запланированная дата вхождения = `nth_occurrence(plan, completed_cycles)` ДО инкремента**
(не `today`!) — иначе double-tap в разные календарные дни даст разные `occurrence_date` и
unique не сработает. Обе колонки NULL для обычных транзакций; индекс полагается на
дефолт Postgres 16 **NULLS DISTINCT** (много NULL-строк допустимо) — **НЕ** ставить
`NULLS NOT DISTINCT` (схлопнет все не-плановые tx в один слот). Повторный confirm того же
вхождения → unique-violation → 409, без двойного дохода и скима ([ADR-0007](./0007-envelopes.md)).
confirm + ським — один `session.commit()`. Автоисполнение и push — **backlog**.

**`next_occurrence` не хранится** (must-fix): выводится `nth_occurrence(plan,
completed_cycles)`. Единственный источник истины — `completed_cycles`. Колонки нет.

**Развёртка вхождений (must-fix #5 + календарная арифметика):** `occurrences(plan, from,
to)` стартует со **следующего неподтверждённого** вхождения = `nth_occurrence(plan,
completed_cycles)`, НЕ с `first_date` (иначе досрочно подтверждённое вхождение, особенно
`week`, двоится в прогнозе). `period`-шаг — **календарный, не timedelta** (`date + int`
бросит TypeError; месяц/год не фиксированы):
```
nth_occurrence(first_date, recurrence, n):   # n = completed_cycles
  once  -> first_date;  генератор отдаёт [] если n>=1
  week  -> first_date + timedelta(weeks=n)
  month -> first_date + relativedelta(months=n)   # 31→28/29/30 (clamp end-of-month)
  year  -> first_date + relativedelta(years=n)
```
**PIN:** monthly-on-31 в феврале клампится на последний день месяца (relativedelta
default). `total_cycles IS NULL` = бесконечно → `remaining_cycles` не вычисляем, клипуем
по `horizon`. Ограничение `remaining_cycles = total_cycles − completed_cycles` только при
`total_cycles IS NOT NULL`.

**Формула прогноза** (`GET /api/forecast?horizon=`, default — конец текущего месяца):
```
available_now       = Σ балансы счетов workspace               (event-sourced, ADR-0004)
reserved            = Σ reserved активных конвертов            (ADR-0007, Σ entries)
planned_income      = Σ planned(kind=income,  status='planned'), вхождения в (today, horizon]
planned_expense     = Σ planned(kind=expense, status='planned'), вхождения в (today, horizon]
planned_skim        = Σ per occurrence per envelope:
                        floor(plan.amount_minor * envelope.percent / 100)
                      (только income-плана × active envelope.percent NOT NULL, v1.1)
projected_balance   = available_now + planned_income − planned_expense
projected_available = projected_balance − reserved − planned_skim   ← заголовок
```

Пины:
- `horizon` default = конец текущего месяца = `date(y, m, last_day)`; окно `(today,
  horizon]` (today исключён слева, последний день включён). `today =
  datetime.now(timezone.utc).date()` — **единый хелпер в `services/forecast.py`**,
  переиспользуется везде (НЕ ссылаемся на `goals.py` — он ретайрится в Phase 6). `date`
  (планы) vs `datetime` (транзакции): сравнение по `date`, балансы — снапшот «сейчас».
- **`horizon` max** = 13 месяцев (защита от раздувания списка вхождений на бесконечном
  weekly-плане); за пределами — caller-trusted, соло-юзер.
- Считаем **только `status='planned'`**. Подтверждённый план уже стал транзакцией →
  в `available_now`; учёт в planned_* = двойной счёт.
- **v1.1:** `planned_skim` зеркалит `skim_on_income` (ADR-0007) — per-occurrence
  per-envelope floor. Прежнее v1-упрощение «не моделируем future skim» создавало gap:
  юзер видел planned_income полностью в available, после confirm'a реально получал
  меньше на сумму скима. Рекурсии prognoz↔skim нет — это чистый доп-вычет; planned_income
  остаётся «грязным» (доход на счёт), planned_skim переносит долю в reserved концептуально
  → projected_available «после скима». Сходимость: Σ floor по конвертам ≤ amount, т.е.
  planned_skim ≤ planned_income; predicted_reserved (existing+future) не считается отдельно.
- **Психология (UI, не формула):** `projected_available > 0` → «Излишек N — в конверт?»;
  `<= 0` → мягкая подача, не красный приговор. Формула честная, переобрамляет presentation.
- Прогноз «по средним за прошлые месяцы» (типовые траты) — упрощённо/опционально для v1.

## Последствия

- Подкатегории (`categories.parent_id`, 2 уровня) — едут вместе с планированием
  (finding #5: планирование требует детализации).
- Календарный вид плана — опционально/backlog; v1 = список по датам + блок прогноза.
- `services/forecast.py` агрегирует балансы + конверты + развёртку планов в одном месте.
