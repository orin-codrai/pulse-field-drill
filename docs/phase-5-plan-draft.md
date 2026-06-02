# Phase 5 — Планирование + прогноз + подкатегории (черновик v2)

> Имплементационный draft. Супер­седит §Phase 5 из [`v1.0-plan.md`](./v1.0-plan.md)
> только в части деталей кода; решения архитектурного уровня — там и в
> [ADR-0008](./adr/0008-planning-forecast.md), не дублируем.
> Статус: **DRAFT v2 — после pass 7 (10 must-fix + 5 consistency applied), ждёт pass 8.**

## 0. Done criteria (из v1.0-плана)

DC#3: «Видеть, что и когда я плачу/получаю; прогноз балансов на 30/60 дней;
явный сигнал что денег нет на запланированную трату.»

Pause point: внести реальный план («квартира 35000/мес с 1 числа»), подтвердить
по факту наступления даты, проверить что прогноз сошёлся.

## 1. Заметки до старта

- Phase 4 уже на VPS: всё键ится на `workspace_id`, `current_workspace` ре-валидирует
  membership, `_validate_*_ref` mirrors есть в `transactions.py`, `goals.py`,
  `budgets.py`. Новые роутеры — копировать паттерн.
- `categories.workspace_id IS NULL` = системная (глобальная). Подкатегория юзера к
  системному родителю — разрешена (например, «Продукты → Овощи»).
- `services/balances.py:account_balance` ретайрится в Phase 6 (вместе с
  `goals` → `envelopes`). В Phase 5 — НЕ трогаем.
- `current_workspace` всегда возвращает Workspace юзера; cross-workspace в
  любом валидаторе → 422/404 (404 для GET-ресурса, 422 для FK-валидации).

## 2. Этапы

### 5.0 Подкатегории

**Модель `Category` (правка):**
```python
parent_id: Mapped[int | None] = mapped_column(
    Integer, ForeignKey("categories.id", ondelete="RESTRICT")
)
```
- `ON DELETE RESTRICT`: удалить родителя с активными детьми нельзя; explicit
  catch в `delete_category` (см. ниже) превращает FK-violation в 409, не 500.
- Глубина-2 — **НЕ через CHECK** (CHECK не видит другую строку). Enforce
  в валидаторе `_validate_parent_ref`. БД глубину **не гарантирует** (миграция
  /bulk insert может нарушить) — задокументировать в docstring модели.

**Общие хелперы (DRY для tx/budget/planned валидаторов) — C4:**
```python
async def _resolve_category(
    session: AsyncSession, category_id: int, workspace_id: int
) -> Category | None:
    """Системная (workspace_id IS NULL) ИЛИ принадлежащая workspace.
    Возвращает None если категория недоступна юзеру. Caller решает 404 vs 422."""
    return await session.scalar(
        select(Category).where(
            Category.id == category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )


async def _resolve_account(
    session: AsyncSession, account_id: int, workspace_id: int
) -> Account | None:
    """Account workspace'a. Возвращает None если не существует или чужой."""
    return await session.scalar(
        select(Account).where(
            Account.id == account_id, Account.workspace_id == workspace_id
        )
    )
```
Переписать `transactions._validate_category_ref`, `budgets._validate_category`
поверх `_resolve_category`; `transactions._validate_account_ref`,
`goals._validate_linked_account` поверх `_resolve_account`. Account и
category — **параллельные** хелперы, не один поверх другого.

**C9-1 — единый шаблон для всех mirrors `_validate_account_ref`** (копируется
в `transactions.py`, `goals.py` как `_validate_linked_account`, `planned.py`):
```python
async def _validate_account_ref(
    session: AsyncSession, account_id: int, workspace_id: int, field: str
) -> None:
    acc = await _resolve_account(session, account_id, workspace_id)
    if acc is None:
        raise HTTPException(422, f"{field}: not found")
    if acc.archived_at is not None:
        raise HTTPException(422, f"{field}: account is archived")
```
Бит-в-бит идентично во всех трёх роутерах. **C10-1:** текущий
`goals._validate_linked_account` имеет сигнатуру `(session, account_id,
workspace_id)` БЕЗ параметра `field` (текст ошибки hardcoded). Refactor
**обязан** добавить `field` параметр: вызов в `goals.py` становится
`await _validate_linked_account(session, id, ws.id, "linked_account_id")`.
Имя функции остаётся `_validate_linked_account` (исторически), сигнатура
выравнивается с template'ом. Поведение не меняется (текст ошибки тот же),
тесты goals не задеваются.

**Сделать в отдельном refactor-коммите ПЕРЕД Phase 5 кодом** — иначе diff будет
шумным.

**`_validate_parent_ref` (включает kind-inheritance из MF7):**
```python
async def _validate_parent_ref(
    session: AsyncSession, parent_id: int, workspace_id: int, child_kind: str,
) -> None:
    parent = await _resolve_category(session, parent_id, workspace_id)
    if parent is None:
        raise HTTPException(422, "parent_id: not found")
    if parent.parent_id is not None:
        raise HTTPException(422, "parent_id: nested deeper than 2 levels")
    if parent.archived_at is not None:
        raise HTTPException(422, "parent_id: parent is archived")
    # kind-inheritance:
    # - parent 'both' → child любого kind разрешён.
    # - parent 'expense'|'income' → child должен совпадать или быть 'both' — НЕТ:
    #   child='both' под non-both parent запрещён (parent narrower чем child).
    if child_kind == "both" and parent.kind != "both":
        raise HTTPException(
            422, "parent_id: child kind='both' requires parent kind='both'"
        )
    if parent.kind != "both" and parent.kind != child_kind:
        raise HTTPException(
            422, f"parent_id: kind mismatch (parent={parent.kind}, child={child_kind})"
        )
```

**Схема `CategoryCreate` (правка):**
- `parent_id: int | None` в `CategoryCreate` (опционально).
- `CategoryUpdate.parent_id` — **не разрешаем** (move между родителями = разные
  семантические категории; пересоздать).

**MF9-1 — правка `app/routers/categories.py:create_category`:**
```python
if body.parent_id is not None:
    await _validate_parent_ref(session, body.parent_id, ws.id, body.kind)
cat = Category(workspace_id=ws.id, **body.model_dump())
```
Без этой проверки `parent_id` юзера принимается на веру; FK на `categories.id`
валидирует только существование строки, не membership → юзер шлёт `parent_id`
чужой категории → запись пройдёт, дерево категорий течёт между workspaces.
Тот же класс багов, что C4 закрыл в transactions/budgets; categories-роутер
ловит сам себя.

**Уникальность имени (C9-2):** `categories_ws_name_uq` partial unique на
`(COALESCE(workspace_id, 0), name) WHERE archived_at IS NULL` существует на
весь workspace, **не учитывая `parent_id`**. Подкатегория с дублирующимся
именем под другим родителем (например, «Продукты → Овощи» и «Кафе → Овощи»)
→ 409. Это **by design**: имя категории должно быть уникальным в workspace,
родитель — только группировка для UI. Если на догфуде окажется неудобно —
расширить индекс на `(workspace_id, COALESCE(parent_id, 0), name)` в Phase 8
(не сейчас, без сигнала).

**C10-2 — system-name vs user-name:** COALESCE(workspace_id, 0) разделяет
namespaces системных (0) и юзеровских (ws.id). Юзер **может** создать свою
«Продукты» при существующей системной «Продукты» — попадают в разные
бакеты, 409 не сработает. В селекторе UI будут две «Продукты» (системная и
юзерская). **By design** — юзер вправе создать собственную копию системной
категории с другими подкатегориями. Тест на это поведение:
`test_user_category_with_same_name_as_system_allowed` (уже есть в
`test_categories.py:test_post_with_same_name_as_system_is_allowed`,
переименовать для ясности или оставить).

**Архивация родителя с активными детьми:** PATCH `archived_at` на родителя →
если `count(children WHERE archived_at IS NULL) > 0` → **409** «archive children
first».

**Delete родителя с детьми (C-PIN-E → MUST-FIX-style consistency):** в
`delete_category` обернуть commit в `try/except IntegrityError`, при
`constraint_name == "categories_parent_id_fkey"` → 409 «cannot delete: has
children». Без этого фронт получит 500.

**Тесты (новые):**
- `test_create_subcategory_happy` — parent активен, kind совпадает.
- `test_create_subcategory_with_foreign_parent_id_422` — POST в workspace A
  с parent_id категории workspace B → 422 «not found» (MF9-1).
- `test_create_subcategory_under_system_parent_works` — родитель `workspace_id IS NULL`.
- `test_create_subcategory_with_kind_mismatch_422` — parent='expense', child='income'.
- `test_create_subcategory_both_under_narrow_parent_422` — child='both', parent='expense'.
- `test_create_subcategory_under_both_parent_any_kind_works`.
- `test_create_grandchild_rejected_422` — `parent_id` указывает на категорию с
  непустым `parent_id`.
- `test_archive_category_with_active_children_409`.
- `test_delete_parent_with_children_returns_409_not_500`.

### 5.1 Модель `planned_operations` + миграция 0004

**Модель:**
```python
class PlannedOperation(Base):
    __tablename__ = "planned_operations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)         # 'income' | 'expense'
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="RUB")
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="RESTRICT")
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    first_date: Mapped[date] = mapped_column(Date, nullable=False)
    recurrence: Mapped[str] = mapped_column(Text, nullable=False)   # 'once'|'week'|'month'|'year'
    total_cycles: Mapped[int | None] = mapped_column(Integer)        # NULL = бесконечно
    completed_cycles: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="planned")
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("kind IN ('income','expense')", name="planned_kind_chk"),
        CheckConstraint("amount_minor > 0", name="planned_amount_chk"),
        CheckConstraint("currency = 'RUB'", name="planned_currency_chk"),
        CheckConstraint(
            "recurrence IN ('once','week','month','year')", name="planned_recurrence_chk"
        ),
        CheckConstraint(
            "status IN ('planned','paused','done')", name="planned_status_chk"
        ),
        CheckConstraint("completed_cycles >= 0", name="planned_completed_chk"),
        CheckConstraint(
            "total_cycles IS NULL OR total_cycles >= 1", name="planned_total_chk"
        ),
        CheckConstraint(
            "total_cycles IS NULL OR completed_cycles <= total_cycles",
            name="planned_cycles_bounds_chk",
        ),
        CheckConstraint(
            "(recurrence = 'once' AND total_cycles IS NULL) OR recurrence <> 'once'",
            name="planned_once_no_total_chk",
        ),
        # MF10-1: category обязательна для ВСЕХ kind. transactions_kind_fields_chk
        # требует category_id IS NOT NULL и для 'income', и для 'expense'
        # (см. models/transaction.py). Если план разрешал бы income без
        # категории, confirm крашнулся бы на CHECK XOR транзакций → 500.
        # Межтабличный инвариант: plan-валидация совпадает с tx-валидацией.
        CheckConstraint(
            "category_id IS NOT NULL",
            name="planned_has_category_chk",
        ),
        Index(
            "planned_ws_status_idx", "workspace_id", "status",
            postgresql_where="archived_at IS NULL",
        ),
    )
```

**Транзакции — расширение для confirm-идемпотентности:**
```python
planned_operation_id: Mapped[int | None] = mapped_column(
    BigInteger, ForeignKey("planned_operations.id", ondelete="SET NULL")
)
occurrence_date: Mapped[date | None] = mapped_column(Date)
```
Unique partial: `(planned_operation_id, occurrence_date)` WHERE
`planned_operation_id IS NOT NULL`. Postgres 16 default — `NULLS DISTINCT`,
много обычных tx с обоими NULL допустимы; partial WHERE отрезает non-plan tx
от индекса полностью.

```python
Index(
    "transactions_planned_uq", "planned_operation_id", "occurrence_date",
    unique=True, postgresql_where="planned_operation_id IS NOT NULL",
)
```

**Миграция 0004 (`0004_planned_operations`):**

Upgrade:
1. `CREATE TABLE planned_operations` (CHECK'и + FK + `planned_ws_status_idx`).
2. `ALTER TABLE categories ADD COLUMN parent_id INTEGER`, FK→categories
   ondelete RESTRICT.
3. `ALTER TABLE transactions ADD COLUMN planned_operation_id BIGINT`,
   FK→planned_operations ondelete SET NULL.
4. `ALTER TABLE transactions ADD COLUMN occurrence_date DATE`.
5. `CREATE UNIQUE INDEX transactions_planned_uq` partial WHERE
   `planned_operation_id IS NOT NULL` — через `op.create_index(...
   postgresql_where=...)` (Alembic 1.18+ умеет это в autogenerate, но для
   consistency с manual-стилем 0003 пишем явно).

**Downgrade (MF10, порядок критичен):**
1. `op.drop_index("transactions_planned_uq", postgresql_where=...)` — **первым**,
   иначе `drop_column` на `planned_operation_id` упадёт «cannot drop column
   because index depends on it».
2. `drop_column transactions.occurrence_date`.
3. `drop_column transactions.planned_operation_id` (FK снимется вместе с колонкой).
4. `drop_column categories.parent_id` (FK снимется).
5. `drop_table planned_operations`.

**Downgrade docstring (C8-3):** «Подтверждённые tx сохраняются (workspace_id,
amount, account, category, occurred_at — всё на месте), но **теряют ссылку на
источник** (`planned_operation_id` и `occurrence_date` дропнуты). Re-confirm
тех же будущих вхождений после повторного upgrade породит дубликаты — это
ожидаемо, downgrade=ручная rollback-операция.»

**env.py `INDEXES_MANUAL_ONLY` (MF10):** обновить набор после миграции:
```python
INDEXES_MANUAL_ONLY = {
    "categories_ws_name_uq",
    "transactions_ws_occurred_idx",
    # autogenerate видит `transactions_planned_uq` (partial с literal WHERE),
    # но не сравнивает predicate корректно во всех версиях — добавляем для
    # консистентности с 0003-стилем (manual create_index).
    "transactions_planned_uq",
}
```

### 5.2 Сервис `services/occurrences.py`

**Чистая (no DB) функция:**
```python
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

def nth_occurrence(first_date: date, recurrence: str, n: int) -> date | None:
    """n-е вхождение (0-indexed) плана. Возвращает None для once с n>=1.

    relativedelta даёт «один шаг от base date», не накопительный clamp:
        date(2026,1,31) + relativedelta(months=1) = 2026-02-28
        date(2026,1,31) + relativedelta(months=2) = 2026-03-31   (восстановлено)
    """
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
    plan: PlannedOperation, window_start: date, window_end: date,
    *, inclusive_start: bool = True,
) -> list[date]:
    """Вхождения плана в окне, начиная со СЛЕДУЮЩЕГО неподтверждённого
    (n = completed_cycles).

    Default `inclusive_start=True` (MF8-3): план на window_start включён.
    Это нужно и для `/due` (план на today должен попасть в due-список), и
    для прогноза — иначе scheduled-today-not-confirmed обязательство
    игнорируется (юзер видит «всё ок» утром 1-го числа, тратит, потом
    confirm квартиры → дыра). exclusive вариант (`(window_start, window_end]`)
    — оставлен на случай специального запроса «только будущее, не сегодня»."""
    if plan.status != "planned" or plan.archived_at is not None:
        return []
    result: list[date] = []
    n = plan.completed_cycles
    while True:
        if plan.total_cycles is not None and n >= plan.total_cycles:
            break
        occ = nth_occurrence(plan.first_date, plan.recurrence, n)
        if occ is None:
            break  # once и n>=1
        if occ > window_end:
            break
        if inclusive_start:
            if occ >= window_start:
                result.append(occ)
        else:
            if occ > window_start:
                result.append(occ)
        n += 1
    return result
```

`dateutil` — добавить в `pyproject.toml` явно (см. §4).

**Тесты (`test_occurrences.py`):**
- `nth_occurrence` once happy, once n=1→None, week n=0/n=4.
- `nth_occurrence_month_31_does_not_stick_to_feb_28` —
  `nth(2026-01-31, "month", 2) == 2026-03-31`.
- `nth_occurrence_year_leap_day` — `2024-02-29 + relativedelta(years=1) == 2025-02-28`.
- `occurrences_in_window`: status='paused'→[], archived→[], once-already-completed→[].
- `occurrences_in_window_inclusive_start_includes_today` — план на today попадает
  в `[today, h]`, не попадает в `(today, h]`.
- `occurrences_in_window_weekly_with_completed_offset` — completed=2,
  window=(today, +30 дней) → правильное смещение.
- `occurrences_in_window_clipped_by_total_cycles`.

### 5.3 CRUD + `/confirm` + `/due`

**Схемы:**
```python
class PlannedOperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # currency не принимаем — фикс RUB через server_default.
    kind: Literal["income", "expense"]
    amount_minor: int = Field(gt=0)
    category_id: int                                 # MF10-1: NOT NULL
    account_id: int
    first_date: date
    recurrence: Literal["once", "week", "month", "year"]
    total_cycles: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=2000)  # C2


class PlannedOperationUpdate(BaseModel):
    """Whitelist PATCH. completed_cycles/status исключены из MF3
    (status управляется через confirm/PATCH `status` отдельно? — да, status в
    списке: planned↔paused. 'done' выставляет только confirm)."""
    model_config = ConfigDict(extra="forbid")
    amount_minor: int | None = Field(default=None, gt=0)
    category_id: int | None = None
    account_id: int | None = None
    first_date: date | None = None
    recurrence: Literal["once", "week", "month", "year"] | None = None
    total_cycles: int | None = Field(default=None, ge=1)
    # 'done' НЕ принимаем; paused→planned = resume (completed_cycles
    # сохраняется, следующий confirm с n=completed_cycles, не сброс).
    status: Literal["planned", "paused"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    archived_at: datetime | None = None
    # completed_cycles, currency, kind — НЕ в schema → extra='forbid' отбивает.
```

**Локальные валидаторы** (C8-1: дублируем mirrors из `transactions.py` —
после C4-рефактора станут поверх `_resolve_account`/`_resolve_category`):
```python
async def _validate_account_ref(
    session: AsyncSession, account_id: int, workspace_id: int, field: str
) -> None:
    """Mirror transactions.py: active (not archived) account workspace'a."""
    acc = await _resolve_account(session, account_id, workspace_id)
    if acc is None:
        raise HTTPException(422, f"{field}: not found")
    if acc.archived_at is not None:
        raise HTTPException(422, f"{field}: account is archived")
```

**Response schema (MF9-2):**
```python
class PlannedOperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    workspace_id: int
    kind: Literal["income", "expense"]
    amount_minor: int
    currency: str
    category_id: int | None
    account_id: int
    first_date: date
    recurrence: Literal["once", "week", "month", "year"]
    total_cycles: int | None
    completed_cycles: int
    status: Literal["planned", "paused", "done"]
    note: str | None
    created_at: datetime
    archived_at: datetime | None
    # created_by_user_id намеренно не отдаём — privacy в shared workspace.
```

**Дополнительная схема для due:**
```python
class DuePlannedItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    planned_operation_id: int
    scheduled_date: date
    amount_minor: int
    kind: Literal["income", "expense"]
    currency: str
    category_id: int | None
    account_id: int
    note: str | None
```

**Роутер `app/routers/planned.py`** (prefix `/planned`):
```python
@router.post("", response_model=PlannedOperationOut, status_code=201)
async def create_planned(body, ws, session):
    await _validate_account_ref(session, body.account_id, ws.id, "account_id")
    if body.category_id is not None:
        await _validate_category_for_planned(session, body.category_id, ws.id, body.kind)
    op = PlannedOperation(workspace_id=ws.id, **body.model_dump())
    session.add(op)
    await session.commit()
    await session.refresh(op)
    return op


@router.get("", response_model=list[PlannedOperationOut])
async def list_planned(
    ws, session,
    status: Literal["planned","paused","done"] | None = None,
    include_archived: bool = False,
):
    ...


@router.patch("/{op_id}", response_model=PlannedOperationOut)
async def update_planned(op_id, body, ws, session):
    op = await session.scalar(
        select(PlannedOperation).where(
            PlannedOperation.id == op_id,
            PlannedOperation.workspace_id == ws.id,
        )
    )
    if op is None:
        raise HTTPException(404, "planned op not found")

    updates = body.model_dump(exclude_unset=True)

    # MF2: запрет изменения first_date после первого confirm — иначе все
    # будущие nth_occurrence пересчитаются от нового базиса и сдвинутся
    # относительно уже подтверждённых tx.
    if "first_date" in updates and op.completed_cycles > 0:
        raise HTTPException(
            422, "cannot change first_date after first confirm"
        )

    # Аналогично — recurrence после confirm меняет шаги для будущих вхождений
    # от уже зафиксированного базиса. Тоже запрет.
    if "recurrence" in updates and op.completed_cycles > 0:
        raise HTTPException(
            422, "cannot change recurrence after first confirm"
        )

    # MF8-4: PATCH с явным `null` в NOT NULL/required FK ловим до commit'a.
    # account_id NOT NULL в модели; category_id IS NULL разрешено только
    # для kind='income' (CHECK planned_expense_has_category_chk).
    if "account_id" in updates and updates["account_id"] is None:
        raise HTTPException(422, "account_id cannot be null")
    if (
        "category_id" in updates and updates["category_id"] is None
        and op.kind == "expense"
    ):
        raise HTTPException(
            422, "category_id cannot be null when kind='expense'"
        )

    # Cross-workspace mirrors:
    if "account_id" in updates:
        await _validate_account_ref(session, updates["account_id"], ws.id, "account_id")
    if "category_id" in updates and updates["category_id"] is not None:
        await _validate_category_for_planned(
            session, updates["category_id"], ws.id, op.kind
        )

    for f, v in updates.items():
        setattr(op, f, v)
    try:
        await session.commit()
    except IntegrityError as e:
        # Defence-in-depth: пропущенный CHECK (currency, recurrence, statuses)
        # маппится в 422 с понятным детайлом, не в 500.
        await session.rollback()
        msg = str(e.orig)
        if "planned_" in msg and "_chk" in msg:
            raise HTTPException(422, "planned op violates a CHECK constraint") from e
        raise
    await session.refresh(op)
    return op


@router.delete("/{op_id}", status_code=204)
async def delete_planned(op_id, ws, session):
    op = await session.scalar(
        select(PlannedOperation).where(
            PlannedOperation.id == op_id,
            PlannedOperation.workspace_id == ws.id,
        )
    )
    if op is None:
        raise HTTPException(404, "planned op not found")
    # FK→tx SET NULL: подтверждённые tx сохраняют сумму, теряют связь с планом.
    await session.delete(op)
    await session.commit()
```

**MF6 — kind-aware валидатор категории для плана:**
```python
async def _validate_category_for_planned(
    session: AsyncSession, category_id: int, workspace_id: int, plan_kind: str,
) -> None:
    cat = await _resolve_category(session, category_id, workspace_id)
    if cat is None:
        raise HTTPException(422, "category_id: not found")
    if cat.archived_at is not None:
        raise HTTPException(422, "category_id: category is archived")
    # Расход в категории 'income' не имеет смысла; 'both' — нейтральна.
    if cat.kind != "both" and cat.kind != plan_kind:
        raise HTTPException(
            422,
            f"category_id: kind={cat.kind} does not match plan kind={plan_kind}",
        )
```
`transactions._validate_category_ref` остаётся либеральным (XOR-CHECK
страхует на уровне БД); plan-валидатор — строгий, потому что confirm создаёт
tx без юзерского ввода kind.

**MF1 — main.py:**
```python
# app/main.py — добавить:
from app.routers import planned, forecast
app.include_router(planned.router,  prefix="/api")
app.include_router(forecast.router, prefix="/api")
```

**MF4 — `GET /api/planned/due`:**
```python
@router.get("/due", response_model=list[DuePlannedItem])
async def due_planned(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> list[DuePlannedItem]:
    """Все вхождения active плана, scheduled_date <= today (включительно).

    Окно: [first_date_or_earlier, today]. inclusive_start=True, чтобы
    сегодняшний план попадал. Возвращается **первое неподтверждённое**
    вхождение каждого плана (хвост покрывается прогнозом).
    """
    today = today_utc()
    plans = (await session.execute(
        select(PlannedOperation).where(
            PlannedOperation.workspace_id == ws.id,
            PlannedOperation.status == "planned",
            PlannedOperation.archived_at.is_(None),
        )
    )).scalars().all()

    items: list[DuePlannedItem] = []
    for p in plans:
        scheduled = nth_occurrence(p.first_date, p.recurrence, p.completed_cycles)
        if scheduled is None:
            continue
        if scheduled <= today:
            items.append(DuePlannedItem(
                planned_operation_id=p.id,
                scheduled_date=scheduled,
                amount_minor=p.amount_minor,
                kind=p.kind,
                currency=p.currency,
                category_id=p.category_id,
                account_id=p.account_id,
                note=p.note,
            ))
    items.sort(key=lambda x: x.scheduled_date)
    return items
```

**Confirm — критическая часть (MF5+8+9):**
```python
CONFIRM_FUTURE_GRACE_DAYS = 7   # MF5

@router.post("/{op_id}/confirm", response_model=TransactionOut, status_code=201)
async def confirm_planned(op_id, ws, session):
    op = await session.scalar(
        select(PlannedOperation).where(
            PlannedOperation.id == op_id,
            PlannedOperation.workspace_id == ws.id,
        )
    )
    if op is None:
        raise HTTPException(404, "planned op not found")
    if op.status != "planned":
        raise HTTPException(409, f"cannot confirm: status={op.status}")
    if op.archived_at is not None:
        raise HTTPException(409, "cannot confirm: archived")

    scheduled = nth_occurrence(op.first_date, op.recurrence, op.completed_cycles)
    if scheduled is None:
        raise HTTPException(409, "no more occurrences")

    # MF5: запрет confirm'a глубоко-в-будущее. Сегодняшний план + grace
    # допускаются (юзер может confirm'нуть в течение недели по факту).
    today = today_utc()
    if scheduled > today + timedelta(days=CONFIRM_FUTURE_GRACE_DAYS):
        raise HTTPException(
            409,
            f"cannot confirm before scheduled date "
            f"(scheduled={scheduled}, today={today}, grace={CONFIRM_FUTURE_GRACE_DAYS}d)",
        )

    # MF9: pre-check existing tx — даёт точный 409 даже если кто-то ручкой
    # вставил tx с такими же (planned_operation_id, occurrence_date).
    # Race-condition страхует unique-index в catch ниже.
    existing = await session.scalar(
        select(Transaction.id).where(
            Transaction.planned_operation_id == op.id,
            Transaction.occurrence_date == scheduled,
        )
    )
    if existing is not None:
        raise HTTPException(409, "occurrence already confirmed")

    # Tx — атомарно с инкрементом completed_cycles.
    tx_payload = {
        "workspace_id": ws.id,
        "kind": op.kind,
        "amount_minor": op.amount_minor,
        "currency": op.currency,                 # MF8: пробрасываем currency.
        "category_id": op.category_id,
        "planned_operation_id": op.id,
        "occurrence_date": scheduled,
    }
    if op.kind == "expense":
        tx_payload["from_account_id"] = op.account_id
    else:
        tx_payload["to_account_id"] = op.account_id

    tx = Transaction(**tx_payload)
    session.add(tx)

    op.completed_cycles += 1
    if op.total_cycles is not None and op.completed_cycles >= op.total_cycles:
        op.status = "done"
    elif op.recurrence == "once":
        op.status = "done"

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        # MF9: asyncpg прокидывает constraint_name через diag — точнее string match.
        constraint = getattr(getattr(e.orig, "diag", None), "constraint_name", None) \
                     or getattr(e.orig, "constraint_name", None)
        if constraint == "transactions_planned_uq":
            raise HTTPException(409, "occurrence already confirmed (race)") from e
        raise

    await session.refresh(tx)
    return tx
```

**Тесты `test_planned.py`:**
- POST/GET/PATCH/DELETE happy.
- POST с чужим `account_id`/`category_id` → 422.
- POST без `category_id` (любой kind) → 422 (MF10-1: NOT NULL для всех kind).
- POST `kind=expense` с category `kind='income'` → 422 (MF6).
- POST `recurrence=once` с `total_cycles` → 422 (CHECK).
- POST `currency='USD'` → 422 (extra='forbid', MF10/C5 sanity).
- PATCH `completed_cycles` → 422 (MF3, extra='forbid').
- PATCH `first_date` после `completed_cycles > 0` → 422 (MF2).
- PATCH `recurrence` после `completed_cycles > 0` → 422 (MF2 mirror).
- PATCH `status='done'` напрямую → 422 (MF3, Literal не пускает 'done').
- Confirm happy: создаёт tx с правильной datой/currency/category, completed=1.
- Confirm `once` → `status='done'`.
- Confirm `month total_cycles=2` × 2 → second `done`, третий 409.
- Confirm идемпотентность: два confirm подряд — второй 409 «occurrence already
  confirmed».
- **Confirm concurrent race (MF8-5):** `TestConcurrencyConfirm` класс с
  `@pytest.mark.no_rollback`, по образцу `TestConcurrency` из
  `test_user_provisioning.py`. Два **отдельных** `async_sessionmaker` поверх
  shared `test_engine` (не shared session, иначе обработчики идут одной
  транзакцией и pre-check во втором детерминированно ловит первый —
  IntegrityError-ветка не покрывается). `asyncio.gather(confirm_via_session_a,
  confirm_via_session_b)` → один 201, один 409 (через unique partial index +
  `constraint_name == "transactions_planned_uq"` в except-ветке).
- Confirm `status='paused'` → 409.
- Confirm чужого `op_id` → 404.
- Confirm в будущее за grace → 409 (MF5).
- Confirm в пределах grace → 201.
- **Boundary тесты (OQ8-2)**: weekly recurrence, confirm на `today + 7d`
  (точно grace) → 201; на `today + 8d` → 409 «cannot confirm before
  scheduled date».
- Delete плана с подтверждёнными tx → tx сохраняются с `planned_operation_id=NULL`.

**`GET /api/planned/due`:**
- `test_due_returns_past_and_today_unconfirmed_only` — план на today попадает,
  завтрашний не попадает.
- `test_due_skips_paused_archived_done`.
- `test_due_cross_workspace_isolation`.

### 5.4 Forecast

**Сервис `services/forecast.py`:**
```python
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
from dataclasses import dataclass
from dateutil.relativedelta import relativedelta

MAX_HORIZON_MONTHS = 13


def today_utc() -> date:
    """Единый источник 'сегодня' для всех прогнозирующих сервисов.
    После retire goals.py (Phase 6) — этот же хелпер."""
    return datetime.now(timezone.utc).date()


def end_of_current_month() -> date:
    t = today_utc()
    return date(t.year, t.month, monthrange(t.year, t.month)[1])


@dataclass(slots=True)
class Forecast:
    available_now: int
    reserved: int                # 0 до Phase 6
    planned_income: int
    planned_expense: int
    projected_balance: int
    projected_available: int
    horizon: date


async def compute_forecast(
    session: AsyncSession, workspace_id: int, horizon: date | None = None,
) -> Forecast:
    today = today_utc()
    h = horizon or end_of_current_month()
    # MAX_HORIZON_MONTHS clamp — раньше всего: защита от раздувания weekly без
    # total_cycles. PIN-A: тихий clamp, не 422.
    max_h = today + relativedelta(months=MAX_HORIZON_MONTHS)
    if h > max_h:
        h = max_h

    balances = await all_balances(session, workspace_id)
    available_now = sum(b.balance_minor for b in balances)
    reserved = 0  # P6: Σ envelopes.reserved WHERE archived_at IS NULL.

    planned_income = 0
    planned_expense = 0
    if h > today:
        plans = (await session.execute(
            select(PlannedOperation).where(
                PlannedOperation.workspace_id == workspace_id,
                PlannedOperation.status == "planned",
                PlannedOperation.archived_at.is_(None),
            )
        )).scalars().all()
        for plan in plans:
            # MF8-3: inclusive_start=True (default) — план на today, не
            # confirmed, попадает в planned_expense. Иначе сегодняшнее
            # обязательство не отражено ни в balance, ни в forecast.
            occs = occurrences_in_window(plan, today, h)
            total = plan.amount_minor * len(occs)
            if plan.kind == "income":
                planned_income += total
            else:
                planned_expense += total

    projected_balance = available_now + planned_income - planned_expense
    projected_available = projected_balance - reserved
    return Forecast(
        available_now=available_now,
        reserved=reserved,
        planned_income=planned_income,
        planned_expense=planned_expense,
        projected_balance=projected_balance,
        projected_available=projected_available,
        horizon=h,
    )
```

**Endpoint `app/routers/forecast.py`:**
```python
router = APIRouter(prefix="/forecast", tags=["forecast"])

@router.get("", response_model=ForecastOut)
async def get_forecast(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
    horizon: date | None = Query(default=None),
):
    return await compute_forecast(session, ws.id, horizon)
```

**Тесты `test_forecast.py`:**
- Empty workspace: available=0, planned=0, projected=0.
- Один income-план monthly: `+amount × count(occurrences)` в окне (C1: окно
  явно `(today, h]` — план на today НЕ считается в forecast'е, он попадает в
  `/due` и трансформируется в tx после confirm).
- Confirmed план не считается в planned_*, попадает в available_now через tx.
- `status='paused'` план: игнорируется.
- `archived_at` план: игнорируется.
- `horizon > today + 13mo` → clamped to today + 13mo.
- `horizon < today`: planned=0, projected=available_now.
- `horizon == today`: план scheduled today попадает в planned_* (inclusive_start),
  будущие — нет. Для пустого планового списка → planned=0.
- `план scheduled today, не confirmed`: попадает И в `/due`, И в `planned_*`
  прогноза → projected_balance отражает обязательство (MF8-3).
- **`test_overdue_uncconfirmed_cycles_not_in_planned_expense` (C9-3)**: monthly,
  first_date = today − 90д, completed=0, horizon = end_of_current_month.
  planned_expense=amount × (только будущие вхождения в окне), просроченные
  3 цикла НЕ суммируются. `/due` показывает first_date (первое неподтв.).
  Фиксирует, что разрыв «overdue → /due, future → forecast» намеренный.
- Cross-workspace: план юзера B не учитывается в forecast'е A.
- `month-on-31` план: в феврале +1 шаг = 28-е, +2 = 31 марта (через
  `occurrences_in_window`, без двойного учёта февраля).

### 5.5 Frontend

**Routing:** новый таб `Планирование` — DC#3 центральная фича, должна быть видна.
TabBar становится 4-таб.

**C3 — fallback на маленьких экранах:** если 4 иконки+текст не влезают в
ширину viewport (iOS SE = 320px), переключаемся на **icon-only labels**, текст
показывается только под активной вкладкой. Проверить на real device после первого
деплоя; если ок на SE — оставляем все 4 с текстом.

**Файлы:**
- `frontend/src/pages/PlanningPage/PlanningPage.tsx`
  - Блок прогноза наверху (большая цифра «Останется N к концу месяца»; цвет —
    нейтральный/мягкий, не красный).
  - Список «к подтверждению» (`GET /api/planned/due`) — кнопка «Подтвердить»
    прямо в строке, POST `/planned/{id}/confirm`.
  - Список «предстоит» (планы со scheduled_date в будущем, группировка по неделям).
  - FAB → AddPlanPage.
- `frontend/src/pages/AddPlanPage/AddPlanPage.tsx`: kind, amount, category
  (с подкатегориями в селекторе — отступ для уровня 2), account, first_date,
  recurrence, total_cycles. MainButton ref-pattern.
- `frontend/src/api/planned.ts` + `forecast.ts` (типы вручную из endpoint'ов).
- TabBar.tsx: добавить 4-й таб, fallback icon-only (см. C3).

**Тесты UI:** не пишем автотесты в v1.0; глаза на pause point.

## 3. Сводный список миграции 0004

| Шаг | Что |
|---|---|
| 1 | CREATE TABLE planned_operations + 9 CHECK + 1 partial index status |
| 2 | ALTER categories ADD COLUMN parent_id INTEGER, FK→categories RESTRICT |
| 3 | ALTER transactions ADD COLUMN planned_operation_id BIGINT, FK→planned_operations SET NULL |
| 4 | ALTER transactions ADD COLUMN occurrence_date DATE |
| 5 | CREATE UNIQUE INDEX transactions_planned_uq partial WHERE planned_operation_id IS NOT NULL |

Downgrade: 5→1, **drop_index ДО drop_column** (MF10).

env.py `INDEXES_MANUAL_ONLY` обновлён (+`transactions_planned_uq`, MF10).

## 4. Зависимости

- **Новая:** `python-dateutil>=2.9` (для `relativedelta`). Зрелая (2003, под
  Apache-2.0), стабильная; 2.9.0 — текущая (релиз 2024-09). Не полагаемся на
  transitive — добавляем явно в `pyproject.toml`:
  ```toml
  dependencies = [..., "python-dateutil>=2.9"]
  ```
  Импорт: `from dateutil.relativedelta import relativedelta`.

## 5. Open questions / PIN'ы (после pass 7)

PIN'ы pass 7 закрыты:
- A (clamp horizon) — оставляем clamp, не 422.
- B (monthly-on-31) — реализация корректна, добавлен тест.
- C (PATCH first_date) — переведён в MUST-FIX, добавлен в `update_planned`.
- D (kind подкатегорий) — закрыт явным правилом в `_validate_parent_ref`.
- E (delete родителя 409) — explicit IntegrityError catch в `delete_category`.

PIN'ы pass 8 закрыты:
- **OQ8-1 (forecast inclusive_start):** выбрано **inclusive_start=True**
  default в `occurrences_in_window` — план scheduled today попадает И в
  due, И в forecast'е planned_*. Иначе сегодняшнее обязательство
  игнорируется прогнозом → ложно высокий projected_balance, юзер
  пере-тратит.
- **OQ8-2 (confirm-grace=7д):** boundary тесты добавлены (today+7→201,
  today+8→409). Grace ровно = weekly period — позволяет «confirm заранее
  за неделю»; не позволяет confirm двух разных вхождений того же плана
  (pre-check на existing tx + completed_cycles инкремент защищают).

## 6. Что НЕ делаем (выписано чтобы reviewer не предложил)

- Push-уведомления о наступлении плана — backlog.
- Автоисполнение плана (confirm без юзера) — backlog.
- Календарный вид планирования — backlog.
- Прогноз «по средним за прошлые месяцы» — упрощённо/опционально.
- Будущие скимы из запланированного дохода в формуле — рекурсия, backlog
  (ADR-0008 явно).
- 3-й уровень категорий — backlog.
- Server-side last-used account — backlog.
- **Overdue (просроченные не-confirmed) циклы в forecast (C9-3)** — backlog.
  Текущее поведение: forecast считает планы только от today вперёд
  (`occurrences_in_window` с `n=completed_cycles` сразу клипует по
  `occ <= window_end`, прошлое отбрасывается естественно). Юзер catch-up'ит
  через `/due`, который показывает первое неподтверждённое каждого плана
  (после confirm — следующее). Аргумент в пользу backlog: добавить overdue
  в `planned_expense` создаст double-count после confirm'a (tx появится в
  `available_now`, но overdue ещё в planned_*). Без чистой формулы лучше
  не трогать. Тест `test_overdue_uncconfirmed_cycles_not_in_planned_expense`
  фиксирует поведение как намеренное.

## 7. Готовность к старту

После reviewer pass 8 + применения remaining fix'ов:

1. **Pre-Phase 5 рефакторинг:** вынести `_resolve_category` (для tx/budgets)
   и `_resolve_account` (для tx/goals) хелперы (C4 + MF8-2), переписать
   валидаторы поверх. Отдельный refactor-коммит.
2. Миграция 0004 (5 шагов + manual partial unique + downgrade-порядок).
3. `services/occurrences.py` + тесты.
4. Модель PlannedOperation + правки Transaction (planned_operation_id,
   occurrence_date, unique index) + правка Category (parent_id).
5. Роутер `planned` со схемами Create/Update (extra=forbid), CRUD, confirm,
   due-эндпоинт.
6. `services/forecast.py` + роутер `forecast`.
7. `app/main.py`: include новых router'ов (MF1).
8. Frontend: PlanningPage, AddPlanPage, 4-й таб (с fallback icon-only).
9. `pyproject.toml` + `uv.lock`: `python-dateutil>=2.9`.
10. Deploy через `deploy.sh` (migrate-smoke сработает на копии — 0004 без
    backfill, smoke ловит SQL-синтаксис, partial-unique декларацию).

PAUSE 5: внести реальный план («квартира 35000/мес с 1 числа»), дождаться
наступления даты или confirm в пределах grace, посмотреть прогноз.
