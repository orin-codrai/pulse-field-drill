# Sprint 3 — Postgres, схема, CRUD

> **Спринт расщеплён на 3a и 3b.** 3a: Postgres + полная 7-таблица миграция + accounts/categories/transactions CRUD + balances + provisioning fix. 3b: тела goals/budgets/reports + frontend интеграция. План ниже — финальный для 3a; план для 3b пишется по закрытию 3a.

## Context

Это первая фаза, где появляется состояние, которое переживает рестарт. До этого момента бэкенд был stateless: валидация initData и эхо обратно. Сейчас вкатываем Postgres, проектируем доменную схему под полный продукт (а не под минимум для текущего спринта), и закрываем CRUD над основными сущностями.

Расширенный скоуп против исходного плана — осознанное решение. Два соображения определяющие:

1. **Догфудинг провалится без полного домена.** Sprint 5 «реально пользуюсь каждый день» работает только если в Mini App есть то, ради чего вообще ставят финтрекеры: счета, цели, бюджеты, история. Без них приложение не используется, и весь field-drill теряет валидационную фазу.

2. **Schema-first > YAGNI для БД.** Migration на «расширить таблицу transactions нулевым полем» дешёв. Migration на «разделить транзакции на 4 типа» — переписывание всего, что уже накопилось. Pulse-инфра будет иметь полную доменную модель селлеров/товаров/заказов с самого начала, и привычку «продумать схему один раз» — отрабатываем здесь.

Цена: оригинальная оценка Sprint 3 была 3-4ч, после ревью plan-reviewer'ом честная — 15-20ч (полная схема + 7 моделей + CRUD + миграции с ручными CHECK/partial-index + race-тест провизионинга + smoke). На «несколько часов в неделю» это 3-5 недель календарно. Чтобы не блокировать догфуд, спринт разделён: **3a** (≈8-10ч: схема + accounts/categories/transactions/balances) и **3b** (≈5-8ч: тела goals/budgets/reports + UI). Полная 7-таблица миграция всё равно в 3a — schema-first аргумент работает для схемы, не для эндпоинтов.

## Зафиксированные решения

### Архитектурные

- **Event-sourced балансы** (см. ADR-0004). Балансы счетов и любая аналитика — derived из транзакций SQL-запросами. В `accounts` хранится только `initial_balance_minor`. Преимущества: ноль рассинхронов, история на любую дату, простая запись. Цена: пересчёт. Для личного учёта (десятки транзакций в месяц) — не проблема. Кэш/materialized view добавляем когда станет узким местом.

- **`amount_minor BIGINT` в копейках, не float**. Никаких `NUMERIC(10,2)` и тем более `REAL`. Сумма транзакции всегда положительна (`CHECK > 0`); направление выражается через `transactions.kind` и пара `from_account_id`/`to_account_id`.

- **`CHECK`-ограничения в БД, не только в приложении.** Семантика валидной транзакции (какой `kind` требует какие FK) кодируется как табличный CHECK. Ручной INSERT, баг в бэкенде, миграция данных — ничего не прорвётся.

- **18 системных категорий**, `user_id IS NULL`. Видны всем. Юзер может создавать свои. Маркетплейс-категории (как у Pulse) — тот же паттерн. **Системность определяется ровно через `user_id IS NULL`**, никакого дублирующего булевого поля — единственный источник истины. `COALESCE`-индекс уже кодирует семантику уникальности.

- **2 дефолт-счёта** при первом /api/me: «Карта» (type=card) и «Наличные» (type=cash). Без них UI «добавить транзакцию» упрётся в пустой список и догфуд не стартует.

- **`receipts` как заглушка** (см. ADR-0005). Таблица создаётся, FK на неё в `transactions` стоит. Endpoints для загрузки/парсинга — в Sprint 7+. Storage backend (Telegram file_id / S3 / Cloud.ru Object Storage) — решение отложено в ADR-0005.

### Технологические

- **SQLAlchemy 2.x async** + **asyncpg** драйвер. Совпадает с Pulse-стеком.
- **Alembic** для миграций. Auto-generate с ручной проверкой каждой.
- **Postgres 16** в docker-compose, named volume `pgdata`.
- **Один сервис backend** общается с БД. Никаких прямых connection-ов из других контейнеров.
- **Валюта: только RUB на этом этапе.** Поле `currency CHAR(3) DEFAULT 'RUB'` есть в каждой таблице с денежными суммами **с CHECK-ограничением `currency = 'RUB'`** — не «на будущее» в смысле «можно вставить USD когда захотим», а в смысле «когда придёт время для мультивалюты, понадобится осознанная schema-миграция, которая снимет CHECK». Мультивалютная логика (конверсии, отчёты в нескольких валютах) — non-goal проекта; cross-currency агрегации в /balances/reports сейчас невозможны by design.
- **Error response format:** дефолтный FastAPI `{"detail": str | list}`. Без RFC 7807, без кастомных code-полей. 422 для валидации (Pydantic), 401/403/404 с человекочитаемым `detail`, 500 — `{"detail": "Internal Server Error"}` без traceback'a в проде. Контракт фиксирован, чтобы фронт в 3b знал что парсить.

## Структура backend/ (дополнения к Sprint 2)

```
backend/
├── alembic.ini                 # Alembic config
├── alembic/
│   ├── env.py                  # async-aware env, читает DATABASE_URL из settings
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_initial_schema.py
│       └── 0002_seed_system_categories.py
├── app/
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py          # async_engine, AsyncSessionLocal, get_session() dep
│   │   └── base.py             # DeclarativeBase, общий metadata
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── account.py
│   │   ├── category.py
│   │   ├── transaction.py
│   │   ├── goal.py
│   │   ├── budget.py
│   │   └── receipt.py
│   ├── schemas/                # pydantic — расширяем
│   │   ├── account.py
│   │   ├── category.py
│   │   ├── transaction.py
│   │   ├── goal.py
│   │   ├── budget.py
│   │   └── report.py
│   ├── routers/                # FastAPI — расширяем
│   │   ├── accounts.py
│   │   ├── categories.py
│   │   ├── transactions.py
│   │   ├── goals.py
│   │   ├── budgets.py
│   │   └── reports.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── user_provisioning.py # upsert user + seed defaults at first /api/me
│   │   ├── balances.py          # derived balance queries
│   │   └── reports.py           # monthly/calendar aggregations
│   └── seed/
│       └── system_categories.py # 18 категорий + сидинг функция
└── tests/
    ├── conftest.py              # single schema; SAVEPOINT rollback per test. Классы TestMigrations + TestConcurrency помечены @pytest.mark.no_rollback и используют отдельный engine.
    ├── test_models.py           # CHECK-constraint smoke tests
    ├── test_accounts.py
    ├── test_categories.py
    ├── test_transactions.py
    ├── test_balances.py         # derived balance correctness
    ├── test_goals.py
    ├── test_budgets.py
    └── test_reports.py
```

## Схема — 7 таблиц

### users (уже есть, расширяем)

```sql
CREATE TABLE users (
  id            bigserial PRIMARY KEY,
  tg_id         bigint UNIQUE NOT NULL,
  first_name    text,
  last_name     text,
  username      text,
  language_code text,
  is_premium    boolean,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
```

Поля `first_name`, `last_name`, `username`, `language_code`, `is_premium` — апсертим из initData на каждый /api/me, чтобы поддерживать актуальность.

### accounts

```sql
CREATE TABLE accounts (
  id                    serial PRIMARY KEY,
  user_id               bigint REFERENCES users(id) ON DELETE CASCADE NOT NULL,
  name                  text NOT NULL,
  type                  text NOT NULL CHECK (type IN ('card','cash','savings','debt','credit')),
  currency              char(3) NOT NULL DEFAULT 'RUB' CHECK (currency = 'RUB'),
  initial_balance_minor bigint NOT NULL DEFAULT 0,
  icon                  text,
  archived_at           timestamptz NULL,
  created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX accounts_user_active_idx ON accounts (user_id) WHERE archived_at IS NULL;
CREATE UNIQUE INDEX accounts_user_name_uq
  ON accounts (user_id, name) WHERE archived_at IS NULL;
```

Типы:
- `card` — банковская карта
- `cash` — наличные
- `savings` — накопления / вклад
- `debt` — мне должны (баланс положительный = люди должны мне)
- `credit` — я должен (баланс отрицательный = я должен; погашаю → растёт к нулю)

### categories

```sql
CREATE TABLE categories (
  id          serial PRIMARY KEY,
  user_id     bigint REFERENCES users(id) ON DELETE CASCADE NULL,  -- NULL = системная
  name        text NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('expense','income','both')),
  icon        text,
  archived_at timestamptz NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX categories_user_name_uq
  ON categories (COALESCE(user_id, 0), name) WHERE archived_at IS NULL;
```

Системность = `user_id IS NULL`. Единственный источник истины — никакого дублирующего `is_system boolean`, чтобы поля не разъехались. `COALESCE(user_id, 0)` в индексе — чтобы две системные категории с одним именем были невозможны (`NULL ≠ NULL` в SQL ломает обычный UNIQUE).

### transactions

```sql
CREATE TABLE transactions (
  id              bigserial PRIMARY KEY,
  user_id         bigint REFERENCES users(id) ON DELETE CASCADE NOT NULL,
  kind            text NOT NULL CHECK (kind IN ('expense','income','transfer','adjustment')),
  amount_minor    bigint NOT NULL CHECK (amount_minor > 0),
  currency        char(3) NOT NULL DEFAULT 'RUB' CHECK (currency = 'RUB'),
  from_account_id int REFERENCES accounts(id) ON DELETE RESTRICT NULL,
  to_account_id   int REFERENCES accounts(id) ON DELETE RESTRICT NULL,
  category_id     int REFERENCES categories(id) ON DELETE RESTRICT NULL,
  receipt_id      bigint REFERENCES receipts(id) NULL,
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  note            text,
  created_at      timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT transactions_kind_fields_chk CHECK (
    (kind='expense'    AND from_account_id IS NOT NULL AND to_account_id IS NULL     AND category_id IS NOT NULL) OR
    (kind='income'     AND from_account_id IS NULL     AND to_account_id IS NOT NULL AND category_id IS NOT NULL) OR
    (kind='transfer'   AND from_account_id IS NOT NULL AND to_account_id IS NOT NULL AND from_account_id <> to_account_id AND category_id IS NULL) OR
    (kind='adjustment'
       AND ( (from_account_id IS NOT NULL AND to_account_id IS NULL)
          OR (from_account_id IS NULL     AND to_account_id IS NOT NULL) )
       AND category_id IS NOT NULL)
  )
);
CREATE INDEX transactions_user_occurred_idx ON transactions (user_id, occurred_at DESC);
CREATE INDEX transactions_from_acc_idx ON transactions (from_account_id) WHERE from_account_id IS NOT NULL;
CREATE INDEX transactions_to_acc_idx   ON transactions (to_account_id)   WHERE to_account_id IS NOT NULL;
CREATE INDEX transactions_receipt_idx  ON transactions (receipt_id)      WHERE receipt_id IS NOT NULL;
```

### goals

```sql
CREATE TABLE goals (
  id                  serial PRIMARY KEY,
  user_id             bigint REFERENCES users(id) ON DELETE CASCADE NOT NULL,
  name                text NOT NULL,
  target_amount_minor bigint NOT NULL CHECK (target_amount_minor > 0),
  currency            char(3) NOT NULL DEFAULT 'RUB' CHECK (currency = 'RUB'),
  target_date         date NULL,
  linked_account_id   int REFERENCES accounts(id) NULL,
  icon                text,
  archived_at         timestamptz NULL,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX goals_user_active_idx ON goals (user_id) WHERE archived_at IS NULL;
```

### budgets

```sql
CREATE TABLE budgets (
  id           serial PRIMARY KEY,
  user_id      bigint REFERENCES users(id) ON DELETE CASCADE NOT NULL,
  category_id  int REFERENCES categories(id) NOT NULL,
  period       text NOT NULL CHECK (period IN ('week','month','year')),
  limit_minor  bigint NOT NULL CHECK (limit_minor > 0),
  currency     char(3) NOT NULL DEFAULT 'RUB' CHECK (currency = 'RUB'),
  starts_on    date NOT NULL,
  ends_on      date NULL,
  archived_at  timestamptz NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT budgets_dates_chk CHECK (ends_on IS NULL OR ends_on > starts_on)
);
CREATE UNIQUE INDEX budgets_active_uq
  ON budgets (user_id, category_id, period) WHERE archived_at IS NULL;
```

Одна активная бюджет-политика на пару (категория × период). Архивные не мешают.

**Семантика `ends_on`:**
- `ends_on IS NULL` — «открытый» бюджет, повторяется на каждый `period` бесконечно. `period_ends_on` в `/api/budgets/status` вычисляется как конец текущего календарного `period` от `starts_on` (например, для `period='month'` и `starts_on='2026-04-15'` — это `2026-04-30`). Каждый новый месяц «сбрасывает» счётчик в `/budgets/status`.
- `ends_on NOT NULL` — кампанийный бюджет на фиксированный промежуток. После `ends_on` бюджет неактивен (фильтруется в `/budgets/status`). Юзер может архивировать.

Это единственный валидный способ интерпретации; `ends_on` **не** является «overrid'ом» для естественного конца периода.

### receipts

```sql
CREATE TABLE receipts (
  id                   bigserial PRIMARY KEY,
  user_id              bigint REFERENCES users(id) ON DELETE CASCADE NOT NULL,
  storage_key          text NOT NULL,
  mime_type            text NOT NULL,
  size_bytes           bigint,
  status               text NOT NULL DEFAULT 'uploaded'
                       CHECK (status IN ('uploaded','parsing','parsed','failed','rejected')),
  parsed_total_minor   bigint,
  parsed_currency      char(3) CHECK (parsed_currency IS NULL OR parsed_currency = 'RUB'),
  parsed_merchant      text,
  parsed_occurred_at   timestamptz,
  parsed_raw           jsonb,
  parse_error          text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  parsed_at            timestamptz NULL
);
CREATE INDEX receipts_user_created_idx ON receipts (user_id, created_at DESC);
CREATE INDEX receipts_processing_idx ON receipts (status) WHERE status IN ('uploaded','parsing');
```

**Sprint 3 — только таблица.** Endpoints в Sprint 7+. FK из transactions.receipt_id стоит.

## Сидинг

### 18 системных категорий (migration 0002)

```python
SYSTEM_CATEGORIES = [
    # Расход (16)
    ("👶", "Дети",                 "expense"),
    ("🏠", "Дом. уют",             "expense"),
    ("💆", "Забота о себе",        "expense"),
    ("💊", "Здоровье",             "expense"),
    ("🍽️", "Кафе и рестораны",     "expense"),
    ("💡", "Коммуналка",           "expense"),
    ("🚙", "Машина",               "expense"),
    ("📚", "Образование",          "expense"),
    ("💳", "Платежи, комиссии",    "expense"),
    ("🎁", "Подарки",              "expense"),
    ("🔔", "Подписки",             "expense"),
    ("🛍️", "Покупки",              "expense"),
    ("🛒", "Продукты",             "expense"),
    ("✈️", "Путешествия",          "expense"),
    ("🎬", "Развлечения",          "expense"),
    ("🚇", "Транспорт",            "expense"),
    # Доход (1)
    ("💰", "Зарплата",             "income"),
    # Корректировка (both)
    ("⚖️", "Корректировка",        "both"),
]
```

### 2 дефолт-счёта при первом /api/me

В `services/user_provisioning.py`:

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

DEFAULT_ACCOUNTS = [
    ("Карта",    "card", "💳"),
    ("Наличные", "cash", "💵"),
]

async def ensure_user_provisioned(session: AsyncSession, tg_user: TelegramUser) -> User:
    # 1. Upsert user — ON CONFLICT по натуральному ключу tg_id.
    user = (await session.execute(
        pg_insert(User)
        .values(
            tg_id=tg_user.id,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            username=tg_user.username,
            language_code=tg_user.language_code,
            is_premium=tg_user.is_premium,
        )
        .on_conflict_do_update(
            index_elements=["tg_id"],
            set_={
                "first_name": tg_user.first_name,
                "last_name": tg_user.last_name,
                "username": tg_user.username,
                "language_code": tg_user.language_code,
                "is_premium": tg_user.is_premium,
                "updated_at": func.now(),
            },
        )
        .returning(User)
    )).scalar_one()

    # 2. Seed default accounts. Partial unique index `accounts_user_name_uq` требует,
    # чтобы `index_where` в ON CONFLICT точно совпадал с предикатом индекса —
    # иначе Postgres падает: "no unique or exclusion constraint matching the ON CONFLICT specification".
    for name, type_, icon in DEFAULT_ACCOUNTS:
        await session.execute(
            pg_insert(Account)
            .values(user_id=user.id, name=name, type=type_, initial_balance_minor=0, icon=icon)
            .on_conflict_do_nothing(
                index_elements=["user_id", "name"],
                index_where=Account.archived_at.is_(None),
            )
        )
    return user
```

**Провизионинг вызывается ровно из `GET /api/me`**, не из общей dependency `current_user`. Причина: dependency не должна выполнять writes на каждом запросе — это и неявно, и racy. Контракт: первое обращение нового юзера обязательно через `/api/me` (фронт уже так делает, см. Sprint 2), на всех остальных endpoint-ах `current_user` делает один read-only `SELECT users WHERE tg_id = $1` и 401-ит если юзера ещё нет.

Защита от race (два параллельных `/api/me` от одного юзера): `partial unique index accounts (user_id, name) WHERE archived_at IS NULL` + `INSERT ... ON CONFLICT DO NOTHING`. БД-уровень. Никаких advisory lock-ов и application-level count.

## API endpoints

**3a vs 3b:** в 3a реализуются полноценно `me`, `accounts`, `categories`, `transactions` + balances. `goals`, `budgets`, `reports` в 3a поднимаются как **пустые 200-стабы** — роутер + response-схема существуют, GET-эндпоинты возвращают `[]` / `{}` соответствующей формы, POST/PATCH/DELETE возвращают `501 Not Implemented`. Тесты для них пишутся в 3b вместе с телами.

```
# Уже есть из Sprint 2:
GET    /api/me                                # + теперь триггерит provisioning

# Accounts
GET    /api/accounts                          # все мои + archived
POST   /api/accounts                          {name, type, currency?, initial_balance_minor?, icon?}
PATCH  /api/accounts/{id}                     {name?, icon?, archived_at?}
GET    /api/accounts/balances                 # {account_id: {balance_minor, name, type}}

# Categories
GET    /api/categories                        ?kind=expense|income|both
POST   /api/categories                        {name, kind, icon?}
PATCH  /api/categories/{id}                   {name?, icon?, archived_at?}

# Transactions
POST   /api/transactions                      {kind, amount_minor, from_account_id?, to_account_id?, category_id?, occurred_at?, note?}
GET    /api/transactions                      ?from=&to=&account_id=&category_id=&kind=&limit=&cursor=
GET    /api/transactions/{id}
PATCH  /api/transactions/{id}                 (только note и occurred_at; сумма/счета/категория — иммутабельны, удаление+создание)
DELETE /api/transactions/{id}

# Goals
GET    /api/goals
POST   /api/goals                             {name, target_amount_minor, target_date?, linked_account_id?, icon?}
PATCH  /api/goals/{id}
DELETE /api/goals/{id}
GET    /api/goals/{id}/progress               {current_minor, target_minor, percent, days_left, on_track}

# Budgets
GET    /api/budgets
POST   /api/budgets                           {category_id, period, limit_minor, starts_on, ends_on?}
PATCH  /api/budgets/{id}
DELETE /api/budgets/{id}
GET    /api/budgets/status                    [{budget_id, category_name, spent_minor, limit_minor, percent, period_ends_on}]

# Reports
GET    /api/reports/month                     ?year=&month=  → {by_category: {...}, by_kind: {...}, total_expense, total_income}
GET    /api/reports/calendar                  ?from=&to=     → [{date, expense, income}]
```

`PATCH /api/transactions/{id}` — только мутабельные поля (`note`, `occurred_at`). Сумма, тип, счета — иммутабельны: чтобы исправить → удалить и создать новую. Это сохраняет историю в чистоте; в Pulse при работе с банковскими/маркетплейс-транзакциями этот паттерн критичен (нельзя пост-фактум менять зафиксированные движения).

### Авторизация per-resource endpoints

Все `/{id}`-endpoint-ы под `/api/{accounts,categories,transactions,goals,budgets}` (GET, PATCH, DELETE) должны **фильтровать по `user_id = current_user.id` в самом SELECT**, не post-fetch:

```python
stmt = select(Account).where(Account.id == account_id, Account.user_id == current_user.id)
```

Mismatch → `404 Not Found`, **никогда не `403`** — чтобы не палить существование чужих ID. На каждый из трёх routers (`accounts`, `categories`, `transactions`) в тестах обязательный кейс «user B GET/PATCH/DELETE для ID, принадлежащего user A → 404». Стабы (`goals`, `budgets`) к 3b — тот же контракт.

Категории — особый случай: системные (`user_id IS NULL`) **read-only для всех**. PATCH/DELETE на системную категорию → `403 Forbidden` (а не 404 — здесь существование строки публично). Тест в `test_categories.py`: «PATCH/DELETE на seed-категорию → 403».

### Pydantic-схемы: whitelist полей

**POST схемы** на запись не принимают `currency` ни в одной таблице — сервер всегда пинит `RUB`. Иначе клиент шлёт `"USD"`, БД ругается CHECK-ом, возвращается 500 IntegrityError вместо красивого 422. По мере введения мультивалюты — открыть поле через миграцию.

**PATCH схемы** — строгий whitelist мутабельных полей, не Optional-копия модели:
- `accounts`: `name?`, `icon?`, `archived_at?`
- `categories` (только когда `user_id = current_user.id`): `name?`, `icon?`, `archived_at?`
- `transactions`: `note?`, `occurred_at?` (см. выше про иммутабельность)
- `goals`, `budgets`: фиксируется в 3b plan

`archived_at: null` для un-archive разрешён намеренно (UI кнопка «вернуть из архива»).

## Балансы и отчёты — derived SQL

### Текущий баланс одного счёта

```sql
SELECT
  a.initial_balance_minor
  + COALESCE(SUM(t_in.amount_minor),  0)
  - COALESCE(SUM(t_out.amount_minor), 0)
  AS balance_minor
FROM accounts a
LEFT JOIN transactions t_in
  ON t_in.to_account_id = a.id
  AND t_in.kind IN ('income','transfer','adjustment')
LEFT JOIN transactions t_out
  ON t_out.from_account_id = a.id
  AND t_out.kind IN ('expense','transfer','adjustment')
WHERE a.id = $1
GROUP BY a.id, a.initial_balance_minor;
```

В `services/balances.py` пишется один раз, переиспользуется в `/balances`, `/goals/{id}/progress`, и в репортах. Не дублируется.

После XOR-CHECK на `adjustment` (см. таблицу `transactions`) каждая adjustment-строка имеет ровно один non-null FK и участвует ровно в одном из join-ов (`t_in` или `t_out`). Семантика `adjustment` — **дельта** (+N или −N к балансу счёта), не «установить ровно N». Reset-семантика не предусмотрена; для пересчёта баланса с известного значения юзер создаёт adjustment на разницу.

Знак дельты кодируется тем, какой FK установлен; `amount_minor` всегда положительный:
- `adjustment` с `to_account_id` set, `from_account_id` null → **+amount_minor** к балансу счёта.
- `adjustment` с `from_account_id` set, `to_account_id` null → **−amount_minor** от баланса.

UI Sprint 3b будет давать две кнопки («корректировка в плюс» / «в минус»), которые транслируются в правильный FK при POST.

### Календарь

```sql
SELECT
  occurred_at::date AS day,
  SUM(CASE WHEN kind='expense' THEN amount_minor ELSE 0 END) AS expense,
  SUM(CASE WHEN kind='income'  THEN amount_minor ELSE 0 END) AS income
FROM transactions
WHERE user_id = $1 AND occurred_at >= $2 AND occurred_at < $3
GROUP BY day
ORDER BY day;
```

### Бюджет: текущий расход в активном периоде

```sql
-- для периода 'month' и сегодняшней даты
SELECT COALESCE(SUM(amount_minor), 0)
FROM transactions
WHERE user_id = $1
  AND category_id = $2
  AND kind = 'expense'
  AND occurred_at >= date_trunc('month', now())
  AND occurred_at <  date_trunc('month', now()) + interval '1 month';
```

## Тесты — что обязательно

**Стратегия фикстур (`tests/conftest.py`):** single Postgres schema создаётся один раз на тест-сессию (alembic upgrade head на ephemeral БД через testcontainers/локальный сервис). Каждый тест оборачивается в SAVEPOINT и rollback-ится в teardown — миллисекундная изоляция без DDL на каждый тест.

Исключение: классы `TestMigrations` (полный `alembic upgrade head` / `downgrade base` цикл) и `TestConcurrency` (race-test для default-accounts) помечены `@pytest.mark.no_rollback` и работают на собственном engine + чистая schema per test. Они медленные, поэтому отделены.

`tests/test_models.py` — БД-уровень, ловят регрессии в CHECK:

- `expense` без `from_account_id` → IntegrityError
- `expense` с `to_account_id` non-null → IntegrityError
- `income` без `to_account_id` → IntegrityError
- `income` с `from_account_id` non-null → IntegrityError
- `transfer` с одинаковыми `from_account_id` и `to_account_id` → IntegrityError
- `transfer` с `category_id` → IntegrityError
- `transfer` без обоих FK → IntegrityError
- `adjustment` с обоими `from_account_id` и `to_account_id` non-null → IntegrityError (XOR enforced)
- `adjustment` с обоими FK null → IntegrityError
- `amount_minor = 0` → IntegrityError (положительность)
- `currency = 'USD'` в любой таблице → IntegrityError
- `budgets.ends_on <= starts_on` → IntegrityError (`budgets_dates_chk`)
- Архивация категории не каскадит на исторические транзакции (FK `ON DELETE RESTRICT`)
- Попытка DELETE категории/счёта с зависимой транзакцией → IntegrityError (RESTRICT)

`tests/test_balances.py` — derived correctness:

- Свежий счёт с `initial_balance=10000` → баланс 10000
- + `expense 3000` → 7000
- + `income 5000` → 12000
- + `transfer 2000 to другой` → 10000 на исходном, +2000 на целевом
- + `adjustment 500 на исходный` → 10500
- Удаление транзакции корректно убирает её влияние

`tests/test_user_provisioning.py`:

- Первый /api/me нового tg_id → создаётся user + 2 default accounts
- Повторный /api/me того же tg_id → user обновляется, accounts НЕ дублируются
- Concurrent /api/me от одного юзера (класс `TestConcurrency`, без SAVEPOINT) → ровно 2 accounts. Защита — partial unique index `accounts (user_id, name) WHERE archived_at IS NULL` + `INSERT ... ON CONFLICT DO NOTHING`, не application-level count
- **Идемпотентность provisioning без ошибок:** второй вызов `ensure_user_provisioned` для того же `tg_id` не должен поднять `ProgrammingError`/`InvalidColumnReference` (типичный симптом отсутствующего `index_where` в `ON CONFLICT`). Тест явно проверяет успешный return, а не только финальное число строк — иначе ошибка в первом INSERT тихо роллбэкнула бы транзакцию.

Также в `tests/test_accounts.py`, `test_categories.py`, `test_transactions.py` обязательный кейс на каждый router: **user B GET/PATCH/DELETE для ID, принадлежащего user A → 404**. В `test_categories.py` дополнительно: **PATCH/DELETE на системную (`user_id IS NULL`) категорию → 403**.

Остальные тесты — стандартные FastAPI endpoint-тесты через TestClient: 401 без auth, 200 happy, 422 на невалидные тела, 404 на чужие ресурсы.

## Изменения вне backend/

### docker-compose.yml — добавить Postgres

```yaml
postgres:
  image: postgres:16-alpine
  restart: unless-stopped
  environment:
    POSTGRES_USER: pulse
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    POSTGRES_DB: pulse_drill
  volumes:
    - pgdata:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U pulse -d pulse_drill"]
    interval: 10s
    timeout: 3s
    retries: 5

backend:
  # ...existing...
  depends_on:
    postgres:
      condition: service_healthy
  environment:
    TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
    PULSE_ENV: ${PULSE_ENV:-prod}
    DATABASE_URL: postgresql+asyncpg://pulse:${POSTGRES_PASSWORD}@postgres:5432/pulse_drill

volumes:
  pgdata:
```

### .env.example — добавить

```
POSTGRES_PASSWORD=changeme_locally
```

VPS-`.env` обновляется руками — генерируется крепкий пароль, кладётся.

### Backend startup — миграции до uvicorn, не в lifespan

Миграции прогоняются **вне FastAPI**, до старта uvicorn. В `backend/Dockerfile` `CMD` становится:

```dockerfile
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

Lifespan в `app/main.py` остаётся пустым или используется только для не-БД setup'а.

Причина — не concurrency, а deadlock: `command.upgrade(...)` из async lifespan, когда `sqlalchemy.url = postgresql+asyncpg://...`, детерминированно вешает event loop (alembic discussions #1483, #1722). Обход через `asyncio.to_thread` требует тащить вторую (sync) копию драйвера ради старта — дороже, чем просто migrate-then-serve. Для multi-replica будущего переедет в init-контейнер/Job; пока одна реплика — CMD достаточно.

Решение зафиксировано в ADR-0006.

## Шаги исполнения (порядок)

0. **Зафиксировать 3a/3b split** в `ROADMAP.md` и `README.md` до начала кода. Sprint 4 scope сужается: бывший UI-scope переезжает в 3b, в Sprint 4 остаётся только tech-debt (SDK миграция + template cleanup + tsc fix — что и так туда привязано в `CLAUDE.local.md`). Sprint 5/6 без изменений. Это коммит-1 спринта, отдельно от инфры.

1. **Postgres в compose** — добавить сервис, поднять локально, проверить `pg_isready`. Volume `pgdata` создан.

2. **Зависимости backend** — `uv add sqlalchemy asyncpg alembic`. Lock-файл закоммитить.

3. **Базовая обвязка БД** — `app/db/session.py` (async_engine, AsyncSessionLocal, dependency `get_session`), `app/db/base.py` (DeclarativeBase). Никаких моделей пока — это инфра.

4. **Alembic init** — `uv run alembic init -t async alembic` (async-шаблон, не дефолтный). Поправить `alembic/env.py`: читать `DATABASE_URL` из `app.config.settings`, `async_engine_from_config` + `connection.run_sync(do_migrations)` (готово в шаблоне). В `context.configure(...)` включить `compare_type=True`, `compare_server_default=True`. **CHECK-constraint comparison Alembic не поддерживает** (issue #1761 всё ещё open) — CHECK + partial-index DDL пишется руками, см. Step 6. `app/models/__init__.py` должен явно импортировать каждый модуль моделей (`from .user import User`, `from .account import Account`, ...), чтобы `import app.models` зарегистрировал все таблицы на `Base.metadata` — иначе autogenerate выдаст пустую миграцию без предупреждения.

5. **Модели SQLAlchemy** — `app/models/*.py`, по одной на таблицу. Импортировать все в `models/__init__.py` чтобы Alembic их видел.

6. **Миграция 0001** — `uv run alembic revision --autogenerate -m "initial schema"`. **Autogenerate не детектит CHECK-constraints (alembic #508/#1761) и partial unique indexes (#750)** — ожидать, что ~80% CHECK/partial-index DDL придётся писать руками: для каждого `CheckConstraint` в моделях добавить `op.create_check_constraint(...)`, для каждого `Index(..., postgresql_where=...)` — `op.create_index(..., postgresql_where=...)`. Конкретный список к ручному дописыванию: `transactions_kind_fields_chk`, currency-CHECK-и на всех таблицах, `accounts_user_name_uq`, `accounts_user_active_idx`, `categories_user_name_uq`, `transactions_from_acc_idx/to_acc_idx/receipt_idx`, `goals_user_active_idx`, `budgets_active_uq`, `receipts_processing_idx`. После применения проверить через `psql \d+ <table>` что constraint-ы и partial-индексы реально на месте.

7. **Применить миграцию локально** — `uv run alembic upgrade head`. Подключиться `psql` и проверить что 7 таблиц на месте, CHECK и индексы тоже.

8. **Миграция 0002 — сидинг категорий.** Data migration: `op.bulk_insert(categories_table, [{user_id: None, name, kind, icon}, ...])`. На downgrade — `DELETE FROM categories WHERE user_id IS NULL` (системные = `user_id IS NULL`, единственный признак).

9. **`user_provisioning` service** — `ensure_user_provisioned(session, tg_user) -> User`: upsert user через `pg_insert(User).on_conflict_do_update(index_elements=["tg_id"], set_={...}).returning(User)`. Для каждого дефолтного счёта — `pg_insert(Account).on_conflict_do_nothing(index_elements=["user_id","name"], index_where=Account.archived_at.is_(None))`. **`index_where` обязателен** — без него `ON CONFLICT` против partial unique index не сматчит constraint и упадёт runtime-ом. Никаких `SELECT count`. Одна транзакция на весь provisioning.

10. **Обновить `auth/deps.py`** — `current_user` остаётся read-only: один `SELECT users WHERE tg_id = $1`, возвращает ORM `User`, 401 если юзера ещё нет. Сигнатура меняется с `-> TelegramUser` на `-> User`. `routers/me.py` теперь сам вызывает `ensure_user_provisioned(session, tg_user)` (не через dependency) и конструирует `TelegramUser`-ответ из ORM `User`. На всех остальных endpoint-ах `current_user: User = Depends(current_user)` — никаких writes на каждый запрос.

11. **Routers — 3a vs 3b:**
    - **3a (с телами + тестами):** `accounts`, `categories`, `transactions`. Pydantic in/out схемы. Каждый router сразу с тестами в `tests/test_<name>.py`. **Каждый router следует правилам из секций «Авторизация per-resource endpoints» и «Pydantic-схемы: whitelist» — это контракт, не рекомендация.** В `categories` router отдельная проверка: при PATCH/DELETE если `category.user_id IS NULL` (системная) — 403 до того как делается мутация.
    - **3a (стабы, без тестов):** `goals`, `budgets`, `reports`. Файл роутера + response-схемы, GET-эндпоинты возвращают `[]` / пустой объект соответствующей формы, POST/PATCH/DELETE — `raise HTTPException(501, "Not implemented in 3a — see Sprint 3b")`. Цель — застолбить URL-пространство и схемы ответов, чтобы фронт мог писаться против stable contract.
    - **3b:** наполнить тела goals/budgets/reports + соответствующие тесты + frontend интеграция.

12. **`services/balances.py`** — функции `account_balance(session, account_id)`, `all_balances(session, user_id)`. Используются в `/accounts/balances`, `/goals/progress`, `/reports`.

13. **`services/reports.py`** — `monthly_report`, `calendar_report`, `budget_status`. Один SQL на функцию.

14. **`backend/Dockerfile` CMD** — `sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"`. Скопировать `alembic.ini` и `alembic/` в образ. `app/main.py` lifespan остаётся пустым. ADR-0006 фиксирует решение.

15. **Локально полный smoke:**
    - `docker compose up -d --build`
    - `curl /api/me` через тестовый initData → user создаётся, accounts тоже
    - `curl /api/accounts` → видны 2 дефолта
    - `curl /api/categories` → 18 системных
    - POST transactions для каждого `kind` → 200 на валидных, 422 на невалидных
    - GET balances → математика правильная

16. **Деплой на VPS** — git pull, `docker compose up -d --build` (--build чтобы пересобрать backend с alembic), посмотреть `docker compose logs backend` — миграции должны пройти.

17. **Frontend — НЕ трогаем в Sprint 3a.** Текущий «Hello, name» в IndexPage остаётся. UI всех новых endpoints — Sprint 3b.

18. **Коммиты — Sprint 3a:**
    - `docs: split sprint 3 into 3a/3b`
    - `feat(infra): postgres in compose`
    - `feat(backend): db setup + alembic (async template)`
    - `feat(backend): domain models (7 tables, full schema)`
    - `feat(backend): initial migration with hand-written CHECK + partial indexes`
    - `feat(backend): seed system categories`
    - `feat(backend): user provisioning + default accounts (idempotent via ON CONFLICT)`
    - `refactor(backend): current_user read-only, /api/me triggers provisioning`
    - `feat(backend): accounts CRUD`
    - `feat(backend): categories CRUD`
    - `feat(backend): transactions CRUD + balances`
    - `feat(backend): stub routers for goals/budgets/reports (3b placeholders)`
    - `chore(backend): migrate-on-start via Dockerfile CMD + ADR-0006`

    **Sprint 3b коммиты** — выносятся в свой план, когда 3a закроется.

## Critical files

**New:**
- `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/0001_*.py`, `backend/alembic/versions/0002_*.py`
- `backend/app/db/session.py`, `backend/app/db/base.py`
- `backend/app/models/*.py` (7 файлов)
- `backend/app/schemas/*.py` (6 файлов — расширения)
- `backend/app/routers/*.py` (6 новых)
- `backend/app/services/user_provisioning.py`, `backend/app/services/balances.py`, `backend/app/services/reports.py`
- `backend/app/seed/system_categories.py`
- `backend/tests/test_models.py`, `test_balances.py`, `test_user_provisioning.py`, `test_accounts.py`, `test_categories.py`, `test_transactions.py`, `test_goals.py`, `test_budgets.py`, `test_reports.py`
- `docs/adr/0004-event-sourced-balances.md` — **что в ADR:** баланс счёта = `initial_balance + Σ(transfers in) − Σ(transfers out)`; пересчёт on-read. Trade-off: проще запись + аудит-история out-of-the-box vs. дороже чтение. Trigger для перехода на materialized cache — `EXPLAIN ANALYZE` балансного SQL `> 50ms` на типичном юзере (десятки тысяч транзакций), не «когда станет много пользователей».
- `docs/adr/0005-receipt-storage-backend.md` — **что в ADR:** решение отложено до Sprint 7+. Кандидаты: (a) Telegram file_id — бесплатно, но привязка к боту, (b) S3 / Cloud.ru Object Storage — стандартно, но платно, (c) местная FS на VPS — простейшее, но не масштабируется. В Sprint 3 — только таблица `receipts` со `storage_key text` (произвольная строка), который интерпретируется решением из 0005.
- `docs/adr/0006-migrations-via-dockerfile-cmd.md` — **что в ADR:** root cause = `alembic.command.upgrade()` блокирующий sync API внутри async FastAPI lifespan создаёт nested event loop через asyncpg драйвер — deadlock. Workaround через `asyncio.to_thread` + sync psycopg драйвер дороже (вторая копия драйвера в образе) чем migrate-then-serve через `CMD`. Trade-off: при multi-replica deployment один из инстансов будет проигрывать гонку и крашиться — на этом этапе переезжаем на init-container.

**Modified:**
- `infra/compose/docker-compose.yml` (postgres service, depends_on healthcheck)
- `backend/pyproject.toml` (sqlalchemy[asyncio], asyncpg, alembic)
- `backend/Dockerfile` (`CMD ["sh","-c","alembic upgrade head && uvicorn ..."]`, копировать `alembic/` и `alembic.ini` в образ)
- `backend/app/main.py` (lifespan остаётся пустым — миграции в CMD)
- `backend/app/config.py` (DATABASE_URL setting)
- `backend/app/auth/deps.py` (`current_user` → read-only, return type ORM `User`, 401 если юзера нет)
- `backend/app/routers/me.py` (использует ORM `User`; сам вызывает `ensure_user_provisioned`)
- `.env.example` (POSTGRES_PASSWORD)
- `ROADMAP.md` (Sprint 3 → 3a/3b split; Sprint 4 scope сужается до tech-debt — SDK миграция + template cleanup + tsc fix; Sprint 5/6 без изменений).
- `README.md` (Status: «Sprint 3a in progress»; frontend SDK в стеке: `@tma.js/sdk-react@^3.0` с пометкой про revert и плановой миграции в Sprint 4; Postgres/asyncpg/Alembic уже в стеке).
- `docs/adr/0001-backend-fastapi.md` (versions note: asyncpg + alembic actual versions, async template).

## Verification

- `uv run pytest -v` — все тесты Sprint 3a зелёные (test_models, test_balances, test_user_provisioning, test_accounts, test_categories, test_transactions). Стабы goals/budgets/reports — без тестов.
- `uv run alembic upgrade head` на чистой БД → 7 таблиц + 18 категорий.
- `uv run alembic downgrade base` **на пустой БД (без данных)** → миграции обратимы. Downgrade с данными не покрыт: FK-цепочка `transactions → receipts → users` требует drop'ов в правильном порядке, autogenerate-ный downgrade при наличии данных может дать FK violation — этот сценарий пока не тестируется.
- В Postgres через psql: `\d+ transactions` показывает все CHECK-constraint-ы текстом; INSERT нарушающий CHECK падает.
- **XOR на adjustment:** INSERT транзакции с `kind='adjustment'`, оба `from_account_id` и `to_account_id` non-null → `IntegrityError: transactions_kind_fields_chk`.
- **Currency lock:** INSERT в любую таблицу с `currency = 'USD'` → IntegrityError.
- `curl /api/me` (валидный initData) → user в БД, 2 accounts автосозданы. Повторный вызов — accounts не дублируются (защита — partial unique index, не application count).
- `curl /api/accounts/balances` → корректный JSON, балансы считаются.
- Создать через curl: expense 3000 → баланс «Карта» падает на 3000. transfer 1000 «Карта» → «Наличные» → две дельты, сумма по всем счетам сохраняется. adjustment +500 на «Карта» → +500. adjustment −300 на «Наличные» → −300.
- **Стабы goals/budgets/reports:** GET → `200` с пустыми ответами (`[]` или `{}` соответствующей формы), POST/PATCH/DELETE → `501 Not Implemented`. Сами URL'ы определены и зарегистрированы в FastAPI router'e.
- **Cross-resource auth:** создать второго пользователя (другой `tg_id`), под его токеном попытаться `GET/PATCH/DELETE /api/accounts/<id-первого-юзера>` → `404`. Аналогично для categories и transactions.
- **System category protection:** под валидным токеном `PATCH /api/categories/<id-системной>` → `403`. `DELETE /api/categories/<id-системной>` → `403`.
- **Provisioning idempotency:** дважды дёрнуть `/api/me` подряд (один tg_id) → оба ответа 200, ни в логах backend, ни в Postgres warning'ов про partial-index ON CONFLICT.
- Регрессия Sprint 1+2: Mini App открывается, «Hello, name» работает, HTTPS живой.
- `docker compose down && docker compose up -d` → данные не теряются (`pgdata` volume), миграции на старте проходят и не зависают (lifespan-deadlock не воспроизводится).

## Открытые вопросы (закроем по ходу)

- **Pagination /transactions.** Cursor-based на `(occurred_at, id)`. Lock-in решения — в коде, не в плане.
- **Soft-delete vs hard-delete для transactions.** Сейчас hard. Если в Sprint 5 при догфуде понадобится «undo» — добавим `deleted_at` миграцией.
- **Goal progress when `linked_account_id` IS NULL.** Что считать прогрессом? Один из вариантов: сумма всех `income` категории "Зарплата" с момента создания цели. Или просто требовать `linked_account_id`. Решим в Sprint 3b при имплементации `/goals/{id}/progress`.
- **Archived accounts в новой транзакции.** БД не запрещает создать `expense from archived_account`. Application-level правило: `POST /api/transactions` с `archived_at IS NOT NULL` счётом → `422 {"detail": "Account is archived"}`. Зафиксируем при имплементации router'а transactions.

## Статус plan-review

Прогнано через `plan-reviewer` дважды. После первого прохода: 7 must-fix + 6 consistency. После второго прохода (с расширенным определением reviewer'а — добавлены секции про prose-invariants, API surface audit, operational shape + self-check): 6 must-fix + N consistency. Все правки применены. Главные результаты обоих ревью:

- **adjustment XOR-CHECK** — исправлен (старая форма `OR` позволяла обоим FK быть non-null, что превращало `adjustment` в `transfer` или давало no-op при `from=to`).
- **Миграции вынесены из FastAPI lifespan в Dockerfile CMD** — async lifespan + `alembic command.upgrade` детерминированно вешает event loop (alembic #1483/#1722).
- **`current_user` снова read-only** — провизионинг вызывается только из `/api/me`; идемпотентность через partial unique index + `ON CONFLICT DO NOTHING`, не через application count.
- **Autogenerate Alembic не детектит CHECK + partial indexes** — ~80% этой части миграции 0001 пишется руками.
- **`currency` теперь enforced на уровне БД** — `CHECK (currency = 'RUB')` на каждой таблице с currency.
- **`is_system` удалён** из categories — системность = `user_id IS NULL`, единственный источник истины.
- **Sprint 3 расщеплён на 3a/3b** — full schema в 3a, тела goals/budgets/reports + UI в 3b.
- **Тесты: SAVEPOINT-стратегия** + отдельный `@pytest.mark.no_rollback` для migrations и concurrency.

Второй проход дополнительно поймал:

- **`ON CONFLICT` против partial unique index требует `index_where`** — без него Postgres падает runtime-ом. Service pseudocode + Step 9 + verification теперь явно указывают `index_where=Account.archived_at.is_(None)`.
- **`compare_check_constraints=True`** — не существующий option Alembic (issue #1761 open), убран из Step 4.
- **Cross-resource ownership check** — добавлена секция «Авторизация per-resource endpoints»; mismatch → 404 (не 403), системные категории → 403.
- **Mass-assignment whitelist** — POST схемы не принимают `currency`; PATCH схемы — строгий whitelist полей.
- **`budgets_dates_chk CHECK (ends_on > starts_on)`** + зафиксирована семантика `ends_on IS NULL` (открытый, рекуррентный) vs `ends_on NOT NULL` (кампания).
- **Системные категории защищены на уровне handler'а** + явные тесты в `test_categories.py`.

Этот документ — финальный для Sprint 3a. План Sprint 3b пишется по закрытию 3a.
