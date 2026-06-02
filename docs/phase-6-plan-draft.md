# Phase 6 — Конверты (pay-yourself-first) (черновик)

> Имплементационный draft. Архитектурный уровень — в [ADR-0007](./adr/0007-envelopes.md)
> и [§Phase 6 v1.0-plan.md](./v1.0-plan.md). Здесь — конкретика модели, миграции,
> сервисов, тестов.
> Статус: **DRAFT v3 — после passes 11+12+13 (9 must-fix + 14 consistency applied), Phase 6 cleared.**

## 0. Done criteria (из v1.0-плана)

DC#4: «Pay-yourself-first работает: указал % → доход автоматически
отщипывается → «доступно» уменьшилось.»

PAUSE 6: создать конверты («НЗ 10%», «отпуск 15%»), внести доход, проверить
auto-skim + «доступно к трате».

## 1. Заметки до старта

- Phase 5 на VPS: tx confirm идемпотентен через
  `transactions_planned_uq`, формула forecast'a уже описана (reserved=0
  заглушка). После P6 — `reserved = Σ envelopes.reserved активных`.
- `goals` сейчас живая таблица: 1 модель, 1 роутер (CRUD + /progress),
  12 тестов в `test_goals.py`. На VPS — пустая (юзер не пользуется),
  миграция 0001 её создала, 0002 ничего не сидит.
- `services/balances.py:account_balance` — used **только** в
  `goals._goal_progress` (linked goal); после retire goals — удаляется.
- `transactions.py` POST tx и `planned.py` confirm — оба создают tx с
  `kind='income'`. Ським должен триггериться из единого сервиса
  `services/envelopes.py:skim_on_income(session, tx)`, вызываемого из
  обоих мест, чтобы они не разъехались (ADR-0007).
- `adjustment` с `to_account_id` — inflow по балансу (растит баланс),
  но **не считается доходом** (MF4 из pass 5). Тест `test_adjustment_does_not_skim`.

## 2. Этапы

### 6.1 Модели + миграция 0005

**Модель `Envelope` (mappping существующей `goals` после rename):**
```python
class Envelope(Base):
    __tablename__ = "envelopes"

    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # NEW: процент ауто-скима. NULL = ручной конверт (без авто-скима).
    percent: Mapped[int | None] = mapped_column(Integer)
    # Цель — опциональна; конверт без цели = просто «спрятать N%».
    target_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, server_default="RUB"
    )
    target_date: Mapped[date | None] = mapped_column(Date)
    icon: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "percent IS NULL OR (percent > 0 AND percent <= 100)",
            name="envelopes_percent_chk",
        ),
        # target_amount_minor теперь nullable — но если задан, >0:
        CheckConstraint(
            "target_amount_minor IS NULL OR target_amount_minor > 0",
            name="envelopes_target_chk",
        ),
        CheckConstraint("currency = 'RUB'", name="envelopes_currency_chk"),
        Index(
            "envelopes_ws_active_idx",
            "workspace_id",
            postgresql_where="archived_at IS NULL",
        ),
        # B1 (pass 6): UNIQUE(id, workspace_id) нужен как target для
        # композитного FK с envelope_entries — иначе денормализация
        # workspace_id на entries дрейфит.
        UniqueConstraint("id", "workspace_id", name="envelopes_id_ws_uq"),
    )
```

**Модель `EnvelopeEntry` (новая):**
```python
class EnvelopeEntry(Base):
    """Леджер: каждый скрим/manual/withdraw — иммутабельная строка.

    workspace_id денормализован (MF2 из pass 5): изоляция не должна
    зависеть от join через envelopes — иначе «забыл guard на
    /entries-эндпоинте → cross-workspace утечка». Композитный FK
    `(envelope_id, workspace_id) → envelopes(id, workspace_id)`
    гарантирует, что entry.workspace_id == envelope.workspace_id (B1).
    """

    __tablename__ = "envelope_entries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    envelope_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    # signed: 'skim'/'manual' положительные, 'withdraw' отрицательные.
    # `reserved = Σ amount_minor` без хирургии знаков.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_transaction_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("transactions.id", ondelete="CASCADE")
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('skim','manual','withdraw')",
            name="envelope_entries_kind_chk",
        ),
        CheckConstraint(
            "(kind = 'withdraw' AND amount_minor < 0) "
            "OR (kind IN ('skim','manual') AND amount_minor > 0)",
            name="envelope_entries_sign_chk",
        ),
        # Композитный FK против дрейфа денормализации:
        ForeignKeyConstraint(
            ["envelope_id", "workspace_id"],
            ["envelopes.id", "envelopes.workspace_id"],
            name="envelope_entries_envelope_fkey",
            ondelete="RESTRICT",
        ),
        # source_transaction_id ondelete='CASCADE': при удалении income-tx
        # связанные skim-entries исчезают → reserved автоматически
        # синхронизируется с balance (юзер «отменил» доход). Альтернатива
        # SET NULL оставляла бы reserved выше — пользователь удивляется.
        Index(
            "envelope_entries_env_idx",
            "envelope_id",
            "workspace_id",  # для агрегации reserved per envelope
        ),
        Index(
            "envelope_entries_source_tx_idx",
            "source_transaction_id",
            postgresql_where="source_transaction_id IS NOT NULL",
        ),
    )
```

**Миграция 0005 (`0005_envelopes_from_goals`):**

Upgrade (MF6: новые constraint'ы добавляются с **временным** `goals_*`
именем до rename, потом переименовываются — иначе при failed-retry ALTER
ADD CONSTRAINT упадёт «already exists with that name» на existing
`envelopes_percent_chk` на таблице `goals`).

1. `ALTER TABLE goals ADD COLUMN percent INTEGER` + CHECK
   `goals_percent_chk` (CHECK `percent IS NULL OR (percent > 0 AND
   percent <= 100)`). Имя временное — переименуем в шаге 9.
2. `ALTER TABLE goals ALTER COLUMN target_amount_minor DROP NOT NULL`.
3. **Заменить CHECK `goals_target_chk` (`target > 0`) на nullable-вариант:**
   `DROP CONSTRAINT goals_target_chk` + `ADD CONSTRAINT
   goals_target_nullable_chk CHECK (target_amount_minor IS NULL OR
   target_amount_minor > 0)`. (Postgres не позволяет ALTER на CHECK —
   только DROP+ADD; имя `goals_target_nullable_chk` временное.)
4. `ALTER TABLE goals DROP COLUMN linked_account_id` (FK на accounts
   снимется вместе).
5. `ALTER TABLE goals RENAME TO envelopes`. **PK constraint всегда
   явный rename** (C6): Postgres 16 rename table **НЕ** переименовывает
   PK constraint — он остаётся `goals_pkey`. Поэтому шаг 9 включает
   `ALTER TABLE envelopes RENAME CONSTRAINT goals_pkey TO envelopes_pkey`.
6. `ALTER INDEX goals_ws_active_idx RENAME TO envelopes_ws_active_idx`
   (pass-6 C3 — переименовать, не drop+create).
7. `ALTER TABLE envelopes ADD CONSTRAINT envelopes_id_ws_uq UNIQUE
   (id, workspace_id)` — target для композитного FK с entries.
8. `CREATE TABLE envelope_entries (...)` с композитным FK на
   `envelopes(id, workspace_id)` + индексами env_idx и source_tx_idx.
9. **Batch RENAME CONSTRAINT** (MF6 + C6 — все имена приводим к
   envelopes_*):
   - `goals_pkey` → `envelopes_pkey`
   - `goals_percent_chk` → `envelopes_percent_chk`
   - `goals_target_nullable_chk` → `envelopes_target_chk`
   - `goals_currency_chk` → `envelopes_currency_chk`
   - `goals_workspace_id_fkey` → `envelopes_workspace_id_fkey`

Downgrade: обратный порядок. Existing envelope_entries удаляются (table drop);
`envelopes` → rename to goals; `target_amount_minor` → NOT NULL (если есть
строки с NULL — fail, downgrade невозможен); добавить обратно
`linked_account_id` nullable INT FK→accounts; `percent` drop column.
**Guard в downgrade: `IF EXISTS (SELECT 1 FROM envelopes WHERE
target_amount_minor IS NULL) → RAISE`** — иначе ALTER COLUMN SET NOT NULL
крашнется.

env.py `INDEXES_MANUAL_ONLY` — без изменений (нет новых expression-индексов).

### 6.2 Сервис `services/envelopes.py:skim_on_income`

```python
"""Auto-skim для активных конвертов при подтверждении дохода.

Вызывается из:
  - transactions.create_transaction (POST tx с kind='income')
  - planned.confirm_planned (когда op.kind == 'income')

Контракт: одна БД-транзакция с income tx; commit делает caller. Сервис
читает активные конверты, вставляет skim-entries в той же session.

`adjustment` с to_account_id ИНГНОРИРУЕТСЯ (MF4): растит баланс, но
не доход. Caller проверяет kind перед вызовом — двойная защита через
`assert tx.kind == 'income'`.
"""

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Envelope, EnvelopeEntry, Transaction


async def skim_on_income(
    session: AsyncSession, tx: Transaction, *, actor_user_id: int
) -> list[EnvelopeEntry]:
    """Вставляет skim-entries для всех активных конвертов workspace.
    Возвращает созданные entries (для тестов и логирования).

    floor(amount * pct / 100) — Σ скимов никогда не превысит доход.

    C12-1: `actor_user_id: int` без Optional — v1 не имеет system-paths,
    skim триггерится из API-обработчика с `current_user`. Если когда-то
    backfill/CLI потребуют NULL — расширить миграцией + UI семантика
    «system». До тех пор закрытый тип защищает от пропуска actor.
    """
    # MF7: raise, не assert — assert оптимизируется PYTHONOPTIMIZE=1.
    # adjustment с to_account_id растит баланс, но НЕ доход (MF4 pass 5).
    if tx.kind != "income":
        raise ValueError(f"skim_on_income invoked on kind={tx.kind!r}")
    if tx.id is None:
        # FK source_transaction_id требует уже-flush'нутый tx.
        await session.flush()

    envelopes = (await session.execute(
        select(Envelope).where(
            Envelope.workspace_id == tx.workspace_id,
            Envelope.archived_at.is_(None),
            Envelope.percent.is_not(None),
        )
    )).scalars().all()

    entries: list[EnvelopeEntry] = []
    for env in envelopes:
        # math.floor через целое деление: amount * pct → int, // 100 → floor.
        skim = (tx.amount_minor * env.percent) // 100
        if skim == 0:
            continue  # маленькая amount * маленький percent → floor=0, entry чище
        entry = EnvelopeEntry(
            envelope_id=env.id,
            workspace_id=tx.workspace_id,
            amount_minor=skim,
            kind="skim",
            source_transaction_id=tx.id,
            # MF1: server-set из current_user — НЕ из body.
            created_by_user_id=actor_user_id,
        )
        session.add(entry)
        entries.append(entry)
    return entries
```

### 6.3 Изменения в `transactions.py`

**6.3.1 Триггер skim в `create_transaction`** после `session.add(tx)` и
**перед** commit'ом:
```python
if body.kind == "income":
    await skim_on_income(session, tx, actor_user_id=user.id)
```
Commit-блок: и tx, и entries уйдут одной atomic-транзакцией.

**6.3.2 MF3 — расширить IntegrityError handler** в `create_transaction`:
```python
except IntegrityError as e:
    await session.rollback()
    msg = str(e.orig)
    if "transactions_kind_fields_chk" in msg: ...   # existing
    if "transactions_" in msg and "_chk" in msg: ...  # existing
    # NEW (MF3): CHECK/FK violations on entries → 422, не 500.
    # C12-2: filter покрывает только текущие имена _chk/_fkey. Если
    # позднее добавится `envelope_entries_*_uq` (partial unique, напр.
    # дедуп skim per (env, tx)), он пройдёт мимо filter → 500 регресс.
    # Расширять mapping вместе с любой новой constraint на entries.
    if "envelope_entries_" in msg and ("_chk" in msg or "_fkey" in msg):
        raise HTTPException(422, "envelope entry constraint violated") from e
    raise
```

**6.3.3 MF2 — запрет DELETE на planned-tx**:
```python
@router.delete("/{tx_id}", status_code=204)
async def delete_transaction(tx_id, ws, session):
    tx = await session.scalar(
        select(Transaction).where(
            Transaction.id == tx_id, Transaction.workspace_id == ws.id
        )
    )
    if tx is None:
        raise HTTPException(404, "transaction not found")
    # MF2 (pass 11): DELETE → CASCADE на skim entries OK, но
    # planned_operations.completed_cycles не откатывается → zombie
    # occurrence (план не вернётся в /due, повторный confirm невозможен).
    # Запрет проще, чем атомарный decrement; юзер должен явно отвязать
    # tx от плана (PATCH planned_operation_id=NULL — будущая фича) перед
    # удалением. Не блокирует use case «удалить ошибочную ручную tx».
    if tx.planned_operation_id is not None:
        raise HTTPException(
            409,
            "cannot delete transaction linked to a plan; "
            "detach from plan or use plan to cancel",
        )
    await session.delete(tx)
    await session.commit()
```
Тест `test_delete_planned_tx_returns_409`.

### 6.4 Изменения в `planned.py`

**6.4.1 Триггер skim в `confirm_planned`** после `session.add(tx)`:
```python
if op.kind == "income":
    await skim_on_income(session, tx, actor_user_id=user.id)
```
Тот же commit-блок. confirm идемпотентен (unique tx) → повторный confirm
не вставит дубль entries (tx-insert упадёт первым на unique partial).

**6.4.2 MF3** — расширить IntegrityError handler в `confirm_planned` тем
же mapping'ом на `envelope_entries_*` → 422 (а не 500).

**6.4.3 C13-1 — Обновить модель `Transaction.planned_operation_id`**
синхронно с шагом 9 миграции 0005: `ondelete='SET NULL'` →
`ondelete='RESTRICT'`. Без этого получится metadata-vs-БД drift —
`alembic check` или test_engine.create_all дадут расхождение.

**6.4.4 C13-4 — IntegrityError handler в `delete_planned`:**
```python
@router.delete("/{op_id}", status_code=204)
async def delete_planned(op_id, ws, session):
    op = await session.scalar(...)
    if op is None:
        raise HTTPException(404, "planned op not found")
    try:
        await session.delete(op)
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        msg = str(e.orig)
        if "transactions_planned_operation_id_fkey" in msg:
            raise HTTPException(
                409,
                "cannot delete plan with confirmed transactions; "
                "archive plan or delete linked transactions first",
            ) from e
        raise
```
Иначе после шага 9 (RESTRICT) Postgres вернёт сырой 500 на DELETE plan
с confirmed-from-plan tx.

### 6.5 Роутер envelopes

`app/routers/envelopes.py` (prefix `/envelopes`):
- `POST /envelopes` — create (name, percent?, target_amount_minor?,
  target_date?, icon?). Нет kind у конверта.
- `GET /envelopes` — list workspace + `reserved_minor` per envelope
  (агрегация Σ amount_minor с фильтром `archived_at IS NULL` на envelope —
  важно: query-filter, не компенсирующая entry, чтобы un-archive
  восстановил резерв без правки истории, MF B2). Default: только active;
  `?include_archived=true` показывает archived с `reserved_minor=0`
  (для history).
- `GET /envelopes/{id}/entries` — список entries конверта, новые сверху.
  `amount_minor` возвращается **signed** (withdraw отрицательный); frontend
  применяет sign-aware форматирование (MF/PIN-C ниже). **C13-3:** запрос
  идёт **без join на envelopes** — `select(EnvelopeEntry).where(
  EnvelopeEntry.workspace_id == ws.id, EnvelopeEntry.envelope_id ==
  envelope_id)`. Денормализация workspace_id (MF2) + композитный FK (B1)
  обеспечивают эквивалентность с join'нутым вариантом без лишнего scan.
  Тест `test_entries_endpoint_cross_workspace_404` остаётся критичным.
- `POST /envelopes/{id}/entries` (**status 201 + EnvelopeEntryOut**,
  C12-4) — manual / withdraw entry (юзер ручкой откладывает или забирает
  из конверта). amount_minor>0 в body; kind='manual'/'withdraw' определяет
  знак: для `{"kind": "withdraw", "amount_minor": 1000}` храним `-1000`.
  `source_transaction_id` всегда NULL (только skim связан с tx).
  **MF1 — `created_by_user_id = current_user.id` server-side**, НЕ из
  body. Pydantic-схема `EnvelopeEntryCreate` без поля + `extra='forbid'`
  отбивает mass-assignment в audit.

**C2 — схема entries (Pydantic):**
```python
EnvelopeEntryKindIn = Literal["manual", "withdraw"]  # skim — server-only

class EnvelopeEntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: EnvelopeEntryKindIn
    amount_minor: int = Field(gt=0)  # всегда положительный в payload (PIN-C)

class EnvelopeEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    envelope_id: int
    workspace_id: int
    amount_minor: int  # signed (withdraw < 0); frontend применяет sign
    kind: Literal["skim", "manual", "withdraw"]
    source_transaction_id: int | None
    created_at: datetime
```

- `PATCH /envelopes/{id}` — name, percent, target_*, icon, archived_at.
  Whitelist + `extra='forbid'`. **percent change** при наличии skim-entries
  НЕ переписывает прошлые скимы (они frozen на момент insert); влияет
  только на будущие income. Тест `test_patch_percent_does_not_alter_past_skims`.
- `DELETE /envelopes/{id}` — **запрет если есть entries** (entries
  immutable, удаление искажает историю баланса). Архивация — допустима.
  Тест `test_delete_envelope_with_entries_returns_409`.

**Семантика withdraw:**
- `amount_minor` в payload **положительный**, body `{"kind":"withdraw","amount_minor":1000}`.
- Роутер сохраняет в БД как `amount_minor=-1000` (CHECK
  `envelope_entries_sign_chk` enforce).
- `reserved = Σ` подсчёт автоматически уменьшит резерв.
- Тест `test_withdraw_reduces_reserved`.

**Cross-workspace guard:** все запросы `WHERE workspace_id = ws.id`,
для `/{id}/entries` — `select(EnvelopeEntry).join(Envelope).where(...)`
с фильтром по обоим scope-key. Тест
`test_entries_endpoint_cross_workspace_404` — критичен, потому что
MF2 (денормализация workspace_id) была введена именно для защиты
от forgotten guard здесь.

### 6.6 Обновление `services/forecast.py`

```python
# До (Phase 5):
reserved = 0

# После (Phase 6):
reserved = await session.scalar(
    select(func.coalesce(func.sum(EnvelopeEntry.amount_minor), 0))
    .join(Envelope, Envelope.id == EnvelopeEntry.envelope_id)
    .where(
        Envelope.workspace_id == workspace_id,
        Envelope.archived_at.is_(None),    # B2 query-filter
    )
)
```
Тест `test_forecast_reserved_aggregates_active_envelopes` + регресс на
test_forecast_overdue_not_counted (reserved логика не должна сломать
прежнюю expense-логику).

### 6.7 Retire `goals` router + service + tests

- `app/routers/goals.py` — удалить.
- `app/main.py` — снять `app.include_router(goals.router)`.
- `app/services/balances.py:account_balance` — удалить (used only by
  goals progress).
- `tests/test_goals.py` — удалить.
- `app/schemas/goal.py` — удалить.
- `app/models/goal.py` — удалить (миграция 0005 переименовывает таблицу,
  но модель остаётся под именем `Envelope` в новом файле; goal.py больше
  не нужен).
- `app/models/__init__.py` — убрать Goal, добавить Envelope + EnvelopeEntry.

**Импорт `Goal`** в коде сейчас (grep):
- `models/__init__.py`
- `routers/goals.py`
- `tests/test_goals.py`
Никаких сторонних рефов нет — retire безопасный.

### 6.8 Frontend

- `EnvelopesPage`: список конвертов с `name`, `reserved_minor`, `percent`,
  optional target progress (`reserved/target` если target задан). Inline
  «+ положить» / «− забрать» с amount input → POST /entries. FAB / MainButton
  «+ Конверт» → AddEnvelopePage.
- `AddEnvelopePage`: name, percent (optional slider/input), target_amount
  (optional), target_date (optional), icon.
- **C4 — `BalancesPage` обновить заголовок**, источники данных:
  - **Всего**: Σ balances (как сейчас, из `/api/accounts/balances`).
  - **Зарезервировано**: `forecast.reserved` из `GET /api/forecast`
    (Phase 5 endpoint; Phase 6 меняет только внутреннюю формулу, API
    surface не меняет).
  - **Доступно**: Всего − Зарезервировано (большая цифра вместо «Общий
    баланс»).
  - EnvelopesPage отдельно зовёт `GET /api/envelopes` для per-envelope
    `reserved_minor` (Forecast суммирует, EnvelopesPage показывает разбивку).
- **PIN-C frontend** — entries display: `amount_minor` signed; UI делает
  `abs()` + цвет по знаку: withdraw красным, manual/skim зелёным.
  Удобный хелпер `formatSignedRub(minor)` в `lib/format.ts`.
- `useEnvelopes` хук (`/api/envelopes`); refetch-key `'envelopes'`.
- 5-й таб? **Нет** — конверты доступны из Балансов (раздел «Конверты»)
  + из Меню. Tab-bar остаётся 4-табовым (Balances/List/Plans/Menu).

### 6.9 Что НЕ делаем (выписано чтобы reviewer не предложил)

- Жёсткая блокировка конвертов (запрет тратить из «доступно к трате»
  если уйдёт в минус) — backlog.
- Реальное движение денег между счёт↔конверт — backlog (виртуальность,
  ADR-0007).
- Σpercent>100% — UI warning, не БД CHECK (юзер сам отвечает; reviewer pass 5).
- Превышение target_amount_minor → нет события, просто визуально >100%
  progress bar.
- 0%-ные скимы — пропускаем (entries чище); если percent=0 невозможен
  через CHECK (`percent > 0`), это insurance check «на всякий».
- Backfill процентов на существующие goals — миграция 0005 их превращает в
  manual конверты (percent=NULL). У юзера сейчас 0 goals → 0 envelopes
  после rename.

## 3. Сводный список миграции 0005

| Шаг | Что |
|---|---|
| 1 | ALTER goals ADD COLUMN percent INTEGER + CHECK percent IS NULL OR (>0 AND <=100) |
| 2 | ALTER goals ALTER COLUMN target_amount_minor DROP NOT NULL |
| 3 | DROP CHECK goals_target_chk + ADD CHECK envelopes_target_chk (nullable variant) |
| 4 | ALTER goals DROP COLUMN linked_account_id |
| 5 | ALTER TABLE goals RENAME TO envelopes + rename PK / indices / FK / CHECK constraint names |
| 6 | ALTER TABLE envelopes ADD CONSTRAINT envelopes_id_ws_uq UNIQUE (id, workspace_id) |
| 7 | CREATE TABLE envelope_entries (kind enum + sign CHECK + composite FK + 2 indices) |
| 8 | RENAME CONSTRAINT goals_* → envelopes_* (pkey/percent/target/currency/workspace_fkey) (C5) |
| 9 | **ALTER transactions: DROP FK transactions_planned_operation_id_fkey + ADD same FK ondelete='RESTRICT' (MF12-1)** |

Downgrade: 9→1 (C13-2). Шаг 9 reverse — ALTER `transactions.planned_operation_id_fkey`
обратно на `ondelete='SET NULL'`. Затем 8→1 как раньше: **drop_table
envelope_entries ПЕРЕД rename + drop column** (FK dependencies). Guard: fail
если есть envelopes с target_amount_minor IS NULL (не вернётся в NOT NULL);
fail если есть entries (drop_table безопасен, но история теряется — RAISE
для сигнала).

**MF12-1 — Симметрия DELETE plan ↔ DELETE planned-tx (через rewrite FK).**
P5-миграция 0004 создала `transactions.planned_operation_id_fkey` с
`ondelete='SET NULL'`. Это допускает обход MF11-2 guard'a:

1. Юзер делает `DELETE /api/planned/{op_id}` (P5 endpoint).
2. Каскадный `SET NULL` обнуляет `transactions.planned_operation_id` на
   всех связанных tx (включая confirmed-from-plan).
3. Юзер делает `DELETE /api/transactions/{tx_id}` — MF11-2 guard
   (`tx.planned_operation_id IS NOT NULL`) больше не срабатывает.
4. Skim entries сносятся через `source_transaction_id ondelete='CASCADE'`.

Миграция 0005 шаг 9 переписывает FK на `ondelete='RESTRICT'`. Теперь
DELETE plan с confirmed tx → 409 «cannot delete plan with confirmed
transactions» (IntegrityError mapping в `delete_planned`). Юзер должен
сначала удалить связанные tx (а они защищены MF11-2 guard'ом — взаимная
блокировка → юзер вынужден архивировать вместо удаления, что и есть
консистентная семантика «history immutable»).

Тест `test_delete_plan_with_confirmed_tx_returns_409`.

**Атомарность 0005 (C12-3).** Все 9 шагов идут в одной alembic-транзакции
(Postgres DDL transactional). Частичный crash — полный rollback; повторный
`alembic upgrade head` стартует с чистой схемы шага 1. Это упрощает retry:
никакой ручной recovery после failed migration не нужен.

**Phase 7.5 hard-purge юзера (MF4 + MF5)** — порядок учитывает смешанные
`ondelete` на entries:
- `envelope_entries.workspace_id` → `RESTRICT` (entry держит workspace)
- `envelope_entries.envelope_id+workspace_id` composite → `RESTRICT` (entry
  держит envelope)
- `envelope_entries.source_transaction_id` → `CASCADE` (DELETE income tx
  каскадом снимает skim entries; путь для tx-DELETE, не для workspace purge)

Из v1.0-plan §7.5: `envelope_entries → transactions → planned_operations
→ accounts/categories/budgets/envelopes → audit_log`. Для P6 это значит:
`DELETE FROM envelope_entries WHERE workspace_id=X` **первым** —
покрывает и `skim`, и `manual`/`withdraw` (денормализация workspace_id
делает purge query-простым, не требует join через envelopes).

## 4. Тесты

**Backend:**
- `test_envelopes.py` (CRUD + entries + cross-workspace):
  - POST create happy (с/без percent, с/без target).
  - POST percent=0 → 422 (CHECK).
  - POST percent=101 → 422.
  - POST target_amount_minor=0 → 422 (CHECK target>0 если non-null).
  - GET list with reserved_minor aggregation.
  - GET list excludes archived by default; include_archived=true returns all.
  - GET /{id}/entries cross-workspace → 404.
  - POST /{id}/entries manual → reserved растёт.
  - POST /{id}/entries withdraw → reserved падает.
  - PATCH percent does not alter past skims.
  - PATCH archived_at → reserved активного workspace падает (query-filter B2).
  - PATCH un-archive → reserved восстанавливается (без хирургии истории).
  - DELETE envelope с entries → 409.

- `test_envelopes_skim.py` (auto-skim):
  - Income tx → один активный конверт 10% → 1 skim entry =
    floor(amount * 10 / 100), source_transaction_id = tx.id.
  - Income tx → нет активных конвертов → 0 entries.
  - Income tx → percent IS NULL конверт → НЕ скимится.
  - Income tx → archived конверт → НЕ скимится.
  - **Adjustment с to_account_id → 0 entries** (MF4, ADR-0007).
  - Multiple конверты → Σ skims = Σ floor; gathered atomically.
  - Confirm плана-дохода → skim триггерится (planned + transactions единый
    путь).
  - DELETE income tx → CASCADE убирает skim-entries → reserved падает.
  - PATCH percent после skim → старые entries не меняются; новый income
    скимится по новому percent.

- `test_forecast.py` (обновить):
  - `test_forecast_reserved_aggregates_active_envelopes` — reserved
    отражает Σ entries.
  - `test_forecast_archived_envelope_not_in_reserved` (B2).

- `test_workspace_isolation.py` (расширить):
  - `test_envelope_entry_workspace_matches_parent` — INSERT в entries с
    `workspace_id != envelope.workspace_id` → FK violation (композитный FK
    B1).

- `test_migration_0005.py` — smoke по образцу 0004: upgrade head + downgrade
  0004; assertion'ы что `envelopes` существует, `goals` нет;
  `envelope_entries` с композитным FK видна в pg_constraint.

**Retire:**
- `test_goals.py` — удалить полностью (12 тестов).

Итог: примерно −12 + 25 = **+13 тестов** относительно текущих 169.

## 5. Готовность к старту

После reviewer pass-cycle (passes 11+) + применения must-fix:

1. Миграция 0005 (7 шагов upgrade + downgrade guard).
2. Модели Envelope + EnvelopeEntry + retire Goal.
3. `services/envelopes.py:skim_on_income`.
4. Роутер envelopes + схемы + удаление goals.
5. Изменения transactions.py + planned.py (вызов skim_on_income при income).
6. Обновление forecast.py (reserved через query).
7. Retire balances.py:account_balance.
8. Frontend EnvelopesPage + AddEnvelopePage + update BalancesPage.
9. Deploy через `./deploy.sh` (migrate-smoke ловит rename + composite FK;
   manual smoke на staging-копии prod рекомендуется потому что rename =
   data preservation, не структурный).

PAUSE 6: создать «НЗ 10%», «Отпуск 15%»; внести «Зарплата 100000» через
доход или confirm плана; проверить:
- НЗ +10000, Отпуск +15000 entries
- reserved=25000
- forecast available=balance-25000

## 6. Open questions / PIN'ы

- **PIN-A:** `source_transaction_id ondelete='CASCADE'` (DELETE income tx
  убирает skim-entries) выбран — обоснование: иначе reserved после
  удаления tx >Σbalance, юзер удивлён. Альтернатива SET NULL оставляет
  entries как «история» — приемлемо для аудита, но непрозрачно для
  «available». PIN: **CASCADE**.
- **PIN-B:** 0%-ный skim (floor amount * 0 / 100 = 0) — пропускаем (нет
  entry). CHECK `percent>0` гарантирует, что 0 не дойдёт до записи,
  но `floor` может дать 0 на маленьких amount (amount * pct < 100). Тест
  `test_skim_amount_floor_zero_no_entry` (amount=50, pct=1 → skim=0,
  entry не пишется).
- **PIN-C:** withdraw amount в body — храним как **отрицательное** в БД
  для прозрачной `Σ`-семантики, юзер шлёт **положительное** в payload.
  Тест `test_withdraw_payload_positive_stored_negative`.
- **PIN-D:** Archive envelope с активными entries — entries сохраняются,
  `reserved` падает до 0 в `/api/envelopes` (active only) и в forecast'е
  (query-filter `WHERE archived_at IS NULL` на envelopes). Un-archive
  восстанавливает (без правки entries). Тест `test_archive_unarchive_round_trip`.
- **PIN-E:** Composite FK target — `UniqueConstraint("id", "workspace_id")`
  на `envelopes`. Postgres требует unique-индекс на target для FK; PK
  `id` сам по себе уже unique, но `(id, workspace_id)` нужен **дополнительно**
  как явный target — Postgres не позволяет FK на «`id` + non-PK column»
  без unique constraint покрывающего обе колонки. Дублирующее покрытие
  на `id` приемлемо (envelopes — low-row-count, write-overhead negligible).
  Test `test_entry_workspace_mismatch_fk_violation` — попытка insert с
  envelope_id+workspace_id, где workspace разный — IntegrityError.

- **PIN-B7 (revisit для Phase 7 sharing, MF8):** Phase 6 — personal-only,
  read-committed гонка между PATCH percent и POST tx безвредна (юзер сам
  и тут и там). Phase 7 sharing: юзер A правит percent на shared workspace,
  юзер B confirm'ит income → B читает старый percent → ским по старому.
  Решение для P7: `SELECT envelopes ... FOR SHARE` в `skim_on_income`
  заставит PATCH percent ждать (или наоборот — `FOR UPDATE` на PATCH).
  Не делаем сейчас, но в backlog Phase 7.

- **PIN-F (UX, pass 13).** MF11-2 + MF12-1 — взаимная блокировка
  «нельзя удалить tx-from-plan; нельзя удалить plan c confirmed tx».
  Семантически корректно (history immutable, ADR-0004), но юзер-новичок
  ожидает «ошибся → удалил». Frontend (§6.8 EnvelopesPage не покрывает,
  но AddPlan/PlanningPage) — показывать 409 как мягкий tooltip с явным
  «→ Архивировать» рядом, не как destructive error. Конкретный текст:
  для DELETE tx «История неизменяема. Перенесите/архивируйте план».
  Для DELETE plan «У этого плана есть подтверждённые транзакции.
  Архивируйте план — он перестанет генерировать новые вхождения».

- **MainButton ref-pattern для AddEnvelopePage (§6.8).** Stale-closure
  ловушка из P3 (см. журнал 2026-05-18) — реальный risk на любой новой
  форме. `AddEnvelopePage` следует ref-pattern из `useMainButton`
  (как `AddTransactionPage` и `AddPlanPage`); не передавать state
  через closure без useRef.

- **C1 — index direction.** `envelope_entries_env_idx` объявлен
  `(envelope_id, workspace_id)` для агрегации Σ amount_minor per envelope
  (`/api/envelopes` reserved_minor). Forecast (`Σ по workspace`) фильтрует
  через envelopes сторону + join, не полагается на этот индекс — ему хватает
  `envelopes_ws_active_idx`. Намеренно не оптимизируем под второй кейс
  (low-row-count, EXPLAIN покажет seq scan безболезненно).

## 7. Риски

- **Rename existing table с FK на неё.** `goals` сейчас не ссылается
  никто (leaf table — D1 фидбека); rename безопасен. Если бы было —
  пришлось бы пере-создавать FK.
- **Atomicity skim+income.** `services/envelopes.skim_on_income` живёт
  в той же session, что caller. Caller commit'ит. Все рискованные
  пути (POST tx, confirm plan) уже имеют commit-блок с IntegrityError
  catch — расширим mapping на `envelope_entries_*_chk` → 422.
- **`source_transaction_id` orphan.** Если backfill добавит entries
  раньше создания tx (test data), FK violation. В реальном коде skim
  идёт после `session.flush()` (см. assert в сервисе), tx.id гарантированно
  есть. Тест `test_skim_requires_tx_id` — assert raises без flush.
