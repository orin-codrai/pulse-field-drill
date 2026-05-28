# ADR 0007 — Конверты (pay-yourself-first): слияние с goals, виртуальный резерв, леджер

- **Status:** Proposed (ждёт plan-reviewer)
- **Date:** 2026-05-28
- **Deciders:** @orrin

## Контекст

Dogfood-вывод: нужны pay-yourself-first конверты (методология «Самый богатый человек
в Вавилоне») — «прятать» часть дохода в именованные конверты с настраиваемым %.
В БД уже есть таблица `goals` (цель накопления), по итогам dogfood'a — мёртвая.
Вопрос: конверты и goals — одна сущность или две?

## Рассмотренные варианты

### Одна сущность `envelopes` (replace goals) + леджер ✅ выбрано

Конверт семантически ⊇ цель: у конверта может быть необязательная целевая сумма/дата.
`goals` — leaf-таблица (на неё нет FK), мёртвая → переосмысляем в `envelopes`.

Поля: `percent` (nullable, `0<percent<=100` — доля авто-скима дохода; NULL = ручной),
`target_amount_minor` (становится nullable), `target_date` (nullable), `icon`,
`archived_at`. **Убираем `linked_account_id`** — «прогресс = баланс целого счёта»
путает: конверт это виртуальный срез *общего* баланса, не отдельный счёт.

Накопленное — **event-sourced** леджером (консистентно с [ADR-0004](./0004-event-sourced-balances.md)):
`envelope_entries(id, envelope_id, workspace_id, amount_minor signed,
kind('skim'|'manual'|'withdraw'), source_transaction_id?, created_by_user_id, created_at)`.
`reserved = Σ amount_minor`. **`workspace_id` денормализован** (must-fix): изоляция не
должна зависеть от join через `envelopes` — иначе «забыл guard на entries-эндпоинте →
cross-workspace утечка». Единый scope-ключ на каждой владеемой таблице.

### Две отдельные сущности (goals + envelopes)

- Дублирование: «отпуск 15%» как goal-с-автопополнением vs envelope неотличимы для юзера.
- Два экрана, две модели прогресса — лишняя сложность в pet-проекте на двоих.

### Конверт = материализованная колонка `reserved_minor`

- `percent` меняется со временем → сумму скима всё равно надо замораживать построчно.
- Колонка-агрегат дрейфует под параллельной записью; леджер — нет (тот же довод, что ADR-0004).

## Решение

Одна сущность `envelopes` (миграция из `goals`) + леджер `envelope_entries`.

**Виртуальность (architecture #1 фидбека):** деньги физически НЕ двигаются между
счетами. Конверт = помеченная часть баланса, вычитаемая из «доступно к трате» и
скрытая из основного обзора. Цель — психологически «спрятать от взора».

**Изоляция (must-fix #2 + B1):** `envelope_entries.workspace_id` денормализован. Чтобы он
не разъехался с родителем — **композитный FK**: `UNIQUE(id, workspace_id)` на `envelopes`,
затем `envelope_entries (envelope_id, workspace_id) → envelopes(id, workspace_id)`. БД
гарантирует `entry.workspace_id == envelope.workspace_id`; денормализация без дрейфа.
Тест `test_envelope_entry_workspace_matches_parent`.

**Ським-на-доход:** при подтверждении `kind='income'` (прямой POST или confirm
плана-дохода) для каждого **активного конверта** (`archived_at IS NULL AND percent IS
NOT NULL`) вставляем `skim`-entry `= floor(income.amount_minor * percent / 100)`,
`source_transaction_id` = id дохода, **в той же БД-транзакции**, что и income (единый
сервис `skim_on_income`). **Ським — только ledger-запись, НЕ создаёт balance-moving
транзакцию** (деньги виртуальны; иначе доход посчитался бы в балансе и ещё раз «уехал»
→ сломал бы `available = Σbal − Σreserved`). **floor** — Σ скимов никогда не превысит
доход. `kind='adjustment'` (даже с `to_account_id` — inflow по балансу) **доходом не
считается и не скимится** (must-fix #4): adjustment поднимает баланс (и «доступно»), но
ноль entries; тест проверяет «баланс вырос, entries == 0». Σ `percent` на уровне БД не
ограничена 100% (юзер сам); при Σ>100% — сигнал в UI.

**Confirm идемпотентен** ([ADR-0008](./0008-planning-forecast.md)): unique
`(planned_operation_id, occurrence_date)` на `transactions` → повторный confirm не
создаст второй income и второй ським.

**Доступно к трате** = `Σ балансы счетов − Σ reserved`, где
`reserved = Σ envelope_entries по конвертам WHERE archived_at IS NULL`.
**Архивный конверт исключается из суммы reserved** (must-fix B2 — через query-filter, НЕ
через компенсирующую entry): леджер остаётся неизменяемым (ADR-0004), un-archive
восстанавливает резерв без хирургии истории. Per-envelope `reserved_minor` на архивном
конверте остаётся нулевым в выдаче активных, история entries цела.

## Последствия

- Миграция `goals→envelopes`: rename таблицы, `+percent`, `target_amount_minor`→nullable,
  `−linked_account_id`; роутер/схемы/тесты переименовать (signature break, без shim).
- Ським триггерится из `transactions` POST и из confirm плана ([ADR-0008](./0008-planning-forecast.md))
  — единый сервис, чтобы не разъехалось.
- Превышение лимита конверта, режимы жёсткой блокировки, реальное движение денег —
  **backlog** (раздел 7 фидбека).
- `target_amount_minor`/`target_date` остаются для конвертов-с-целью (прогресс
  `reserved/target`), но это вторично к pay-yourself-first.
