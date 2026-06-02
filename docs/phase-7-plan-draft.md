# Phase 7 — Sharing + регистрация + soft-delete + audit_log

> Статус: **DRAFT v4 — после passes 14+15+16+17 (10 MF + 15 C applied). Phase 7 cleared, старт кода 7.A.**

## Context

Phase 7 — последняя видимая фичевая фаза перед FINAL GATE (v1.0). Цель: закрыть
DC#1 «Можно вести совместный учёт вдвоём». Сейчас весь продукт single-user,
несмотря на готовый workspace-foundation (P4: `current_workspace` с ре-валидацией
membership каждый запрос, switch-PATCH с membership-guard до записи, kind invariant).
Pre-existing infrastructure ждёт sharing-фич: `created_by_user_id` колонки на
`accounts`/`transactions`/`planned_operations`/`envelope_entries` уже добавлены
с `ondelete='SET NULL'` (в миграциях 0003-0005) — комментарии в моделях прямо
говорят «заполнять с Phase 7». P7 эту работу закрывает + добавляет invites,
audit_log, registration (display_name/email/consent_at для compliance), soft-delete
с 30-дневным restore-окном.

Это **самая большая фаза по объёму кода** в v1.0-плане (≈2× P6) — нужно разбить
на 5 этапов с pause-point после каждого (по образцу P5/P6). До старта кода —
reviewer-marathon (passes 14+, ожидаемо 2-3 прохода по track record sotrgo).

User-decisions, зафиксированные через AskUserQuestion перед написанием этого плана:
- **Audit_log в P7** (а не backlog): UI «История изменений» в shared — смысловое
  ядро доверия между двумя пользователями.
- **Registration enforce только на sharing-операциях**: `registered_user` dependency
  на POST workspaces/invites/accept; personal-CRUD (tx/accounts/envelopes/planned)
  не блокируем. Frontend всё равно редиректит на /register на всех страницах через
  App-уровень.
- **Restore не восстанавливает membership в shared** (PIN-G): после soft-delete A
  + restore — возвращается только personal; для shared B должен пригласить заново.
  Логика: A мог удалиться именно чтобы выйти из shared.

---

## 0. Done criteria (из v1.0-plan §Phase 7)

DC#1: «Можно вести совместный учёт вдвоём». Юзер A создаёт shared workspace,
приглашает B через deep-link, оба видят одни и те же данные в shared, personal
остаются независимыми. + Registration fields для compliance, soft-delete с
restore, audit_log с UI «История изменений» в shared.

**PAUSE 7:** реально пригласить партнёра (или второй TG-аккаунт), вести
совместный учёт ≥ 3 дня, проверить: B принимает deep-link → видит данные A, A
видит транзакции B, переключение personal↔shared, audit показывает обоих,
soft-delete + restore на тестовом аккаунте.

---

## 1. Что уже есть и переиспользуем

- **P4 foundation готов:** `auth/deps.py:current_workspace` ре-валидирует
  membership каждый запрос; `routers/workspaces.py` имеет GET (список через join)
  + PATCH `/active` с membership-guard ДО записи.
- **`created_by_user_id` уже в БД на 4 таблицах** (`accounts`/`transactions`/
  `planned_operations`/`envelope_entries`), FK `ondelete='SET NULL'`. Сейчас
  заполняется ТОЛЬКО на `envelope_entries` (manual из `routers/envelopes.py`,
  skim из `services/envelopes.py:skim_on_income` через `actor_user_id`).
  Phase 7 заполняет остаток.
- **`current_user` dependency** возвращает ORM `User` — легко расширяется.
- **Provisioning** (`services/user_provisioning.py`) с advisory-lock на tg_id;
  расширим для registration-flow без переписывания.
- **TG SDK start_param чтение** уже есть в `frontend/src/index.tsx:22`
  (`retrieveLaunchParams().tgWebAppStartParam`) — паттерн установлен.
- **kind='personal'|'shared' CHECK invariant** на Workspace.
- **`schemas/user.py:TelegramUser`** с `extra="ignore"` — переживёт расширение TG.

---

## 2. Этапы

### 7.A — Модели + миграция 0006 + retire-prep

#### Модель `WorkspaceInvite` (новая, `app/models/workspace_invite.py`)

```python
class WorkspaceInvite(Base):
    __tablename__ = "workspace_invites"
    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','accepted','revoked','expired')",
            name="workspace_invites_status_chk",
        ),
        CheckConstraint(
            "(status = 'accepted' AND accepted_by_user_id IS NOT NULL "
            "                     AND accepted_at IS NOT NULL) "
            "OR (status <> 'accepted' AND accepted_at IS NULL)",
            name="workspace_invites_accepted_consistency_chk",
        ),
        Index("workspace_invites_ws_idx", "workspace_id"),
    )
```

- `workspace_id ondelete='CASCADE'`: hard-purge workspace удаляет invites вместе.
- Token: `secrets.token_urlsafe(32)` → ~43 ASCII (TG `startapp` лимит ~64,
  влезает с запасом). UNIQUE защищает от теоретической коллизии — retry 3×.
- TTL: 7 дней.
- Cap shared ≤ 2 (ADR-0009 §5) — enforced application-level в `accept_invite`.

#### Модель `AuditLog` (новая, `app/models/audit_log.py`)

```python
class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    # C14-3: НЕ FK — entity может быть hard-purged (transactions удаляются по
    # workspace_id IN personal_ws); audit живёт через snapshot_json. BigInteger
    # вмещает обе колонки (transactions.id=BigInteger, accounts.id=Integer).
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('transaction','account')",
            name="audit_log_entity_type_chk",
        ),
        CheckConstraint(
            "action IN ('create','update','delete')",
            name="audit_log_action_chk",
        ),
        Index("audit_log_ws_created_idx", "workspace_id", "created_at"),
        Index("audit_log_entity_idx", "workspace_id", "entity_type", "entity_id"),
    )
```

- `workspace_id ondelete='RESTRICT'` (не CASCADE): audit = история, hard-purge
  явно удаляет audit ПЕРЕД workspace.
- `snapshot_json` — after-state на момент действия (для delete — before-state).
  Поле `actor_name_snapshot` внутри snapshot — fallback для UI после SET NULL.
- v1 покрывает `transaction` + `account` (ADR-0009 §6); расширение в backlog.

#### `User` расширение (`app/models/user.py`)

Добавить 4 nullable колонки:
```python
display_name: Mapped[str | None] = mapped_column(Text)
email: Mapped[str | None] = mapped_column(Text)
consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

`consent_at` — timestamp (не bool), несёт момент согласия для compliance.
`deleted_at IS NOT NULL` — soft-delete; 30-дневное окно до hard-purge.

#### `app/models/__init__.py` — добавить imports + `__all__`.

#### Миграция 0006 (`backend/alembic/versions/0006_sharing_and_audit.py`)

6 шагов upgrade в одной alembic-транзакции (Postgres DDL transactional, crash =
полный rollback):

| Шаг | Что |
|---|---|
| 1 | ALTER users ADD COLUMN display_name TEXT NULL |
| 2 | ALTER users ADD COLUMN email TEXT NULL |
| 3 | ALTER users ADD COLUMN consent_at TIMESTAMPTZ NULL |
| 4 | ALTER users ADD COLUMN deleted_at TIMESTAMPTZ NULL |
| 5 | CREATE TABLE workspace_invites + workspace_invites_ws_idx |
| 6 | CREATE TABLE audit_log + audit_log_ws_created_idx + audit_log_entity_idx |

Downgrade 6→1 с 4 guards (RAISE если есть audit/accepted invites/soft-deleted/
consent_at — compliance loss). Без backfill: всё nullable.

`env.py:INDEXES_MANUAL_ONLY` — без изменений (нет partial/expression индексов).

#### `test_migration_0006.py` smoke (по образцу 0005)

Upgrade head → проверки структуры (tables exist, FK directions, constraint
имена). Downgrade с проверкой guards (insert audit row → downgrade RAISE).

Поправить **`test_migration_0005`** (как делали с 0003/0004): explicit revisions
`"0005_envelopes_from_goals"` вместо `"head"`/`"-1"`.

#### conftest TRUNCATE расширить

```
USER_TABLES += ["audit_log", "workspace_invites"]
```
(перед `workspace_members`, `workspaces`).

То же в `test_user_provisioning.TestConcurrency` и `test_planned.TestConcurrencyConfirm`.

#### Pre-Phase 7 refactor (отдельный коммит ДО 7.A?)

В рамках 7.A или отдельным коммитом добавить `current_user` Depend в:
- `routers/accounts.py:create_account, update_account`
- `routers/planned.py:create_planned, update_planned, delete_planned`
- `routers/transactions.py:update_transaction, delete_transaction`
  (create уже имеет — для skim_on_income).

Это standalone-рефакторинг (поведение не меняется до 7.C); полезно для:
(а) более чистого diff в 7.C (`created_by_user_id` заполнение + audit вызовы);
(б) тестируется существующими 194 тестами — sanity-check, ничего не сломано.

**Решение:** включить в 7.A коммит (это всё ещё «pre-fill подготовка»). Альтернатива
— отдельный коммит как pre-P5 refactor `d52378e` — но scope мельче.

### 7.B — services/audit_log.py + services/invites.py

#### `services/audit_log.py` (новый)

```python
"""Audit-log сервис. Вызывается из routers/transactions.py + routers/accounts.py
на create/update/delete. ADR-0009 §6.

Контракт:
- Одна БД-транзакция с основной мутацией (commit делает caller).
- actor_user_id из current_user (server-side).
- snapshot_json — after-state сущности (для delete — before-state).
- actor_name_snapshot внутри snapshot для fallback после hard-purge → SET NULL.
"""

def _serialize_transaction(tx) -> dict: ...     # id, kind, amount_minor, ...
def _serialize_account(acc) -> dict: ...        # id, name, type, ...

_SERIALIZERS = {"transaction": _serialize_transaction, "account": _serialize_account}


async def log_action(
    session: AsyncSession,
    *,
    workspace_id: int,
    actor: User,
    entity_type: str,
    entity_id: int,
    action: str,
    entity,
) -> AuditLog:
    serializer = _SERIALIZERS.get(entity_type)
    if serializer is None:
        raise ValueError(f"unknown entity_type for audit: {entity_type!r}")
    actor_name = actor.display_name or actor.first_name or actor.username or f"tg:{actor.tg_id}"
    snapshot = {"after": serializer(entity), "actor_name_snapshot": actor_name}
    row = AuditLog(
        workspace_id=workspace_id, actor_user_id=actor.id,
        entity_type=entity_type, entity_id=entity_id,
        action=action, snapshot_json=snapshot,
    )
    session.add(row)
    return row
```

#### `services/invites.py` (новый)

`create_invite`:
- Caller гарантирует `workspace.kind == 'shared'` (роутер 403'ит ДО вызова).
- Token: `secrets.token_urlsafe(32)`. Retry 3× на UNIQUE collision (defensive).
- `expires_at = now + 7d`.

`accept_invite` — **критическая race-protection**. MF14-1: workspace lock
**ПЕРВЫМ**, invite lock ВТОРЫМ — глобально стабильный alphabetical-by-table
порядок (защита от deadlock'a между concurrent accept и потенциальным
hard_purge cascade через `workspace_invites` CASCADE).
```python
# ШАГ 0: MF15-1 — advisory_xact_lock на user.id ДО workspace lock'a.
# Lock на одном workspace НЕ сериализует accept'ы одного юзера на разные
# workspaces (cap_shared_per_user race: B accept'ит W1 и W2 параллельно
# → каждый видит count=2, оба commit'ятся → B в 4 shared). Advisory-lock на
# user.id сериализует ВСЕ accept'ы одного юзера; разные юзеры не блокируются.
# Auto-release на commit/rollback (xact_lock). Тот же паттерн, что
# user_provisioning advisory_xact_lock(tg_id) в P4.
_ACCEPT_LOCK_NS = 0x70_75_6c_73_65_61_63  # 'pulseac' — namespace для accept-lock
await session.execute(
    text("SELECT pg_advisory_xact_lock(:key)"),
    {"key": _ACCEPT_LOCK_NS ^ accepting_user.id},
)
# ШАГ 1: read token → workspace_id (без lock, row-shadow OK).
ws_id = await session.scalar(
    select(WorkspaceInvite.workspace_id).where(WorkspaceInvite.token == token)
)
if ws_id is None:
    raise InviteError("not_found", ..., 404)
# ШАГ 2: lock workspace — для cap_workspace race (cap=2 членов).
await session.execute(
    select(Workspace.id).where(Workspace.id == ws_id).with_for_update()
)
# ШАГ 3: lock invite ВТОРЫМ + re-read.
invite = await session.scalar(
    select(WorkspaceInvite).where(WorkspaceInvite.token == token).with_for_update()
)
# Lazy expire:
if invite.status == "pending" and invite.expires_at < now:
    invite.status = "expired"; await session.flush()
    raise InviteError("expired", ..., 410)
if invite.status != "pending":
    raise InviteError("not_pending", ..., 409)
# Already member?
existing = await session.scalar(select(WorkspaceMember).where(...))
if existing: raise InviteError("already_member", ..., 409)
# Cap-2 под workspace lock.
member_count = await session.scalar(select(func.count(WorkspaceMember.id)).where(...))
if member_count >= SHARED_WORKSPACE_CAP:  # C16-1: именованная константа, не magic 2
    raise InviteError("cap_reached", ..., 409)
# MF14-4: cap shared-per-user проверка (защита от 100+ workspace в switcher).
user_shared_count = await session.scalar(
    select(func.count(WorkspaceMember.id))
    .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
    .where(
        WorkspaceMember.user_id == accepting_user.id,
        Workspace.kind == "shared",
    )
)
if user_shared_count >= 3:  # SHARED_PER_USER_CAP
    raise InviteError("user_cap_reached", ..., 409)
# Atomic insert membership + mark invite accepted.
# C17-2: role='member' хардкод; P7 ролей не использует, owner-роль исторична
# (provisioning'овский personal owner). Backlog (3+ участников / admin): при
# PIN-P scenario (inviter soft-deleted → A's owner-row снят → B становится
# единственным членом БЕЗ owner'a) потребуется path «promote to owner» при
# single-member dangling. v1 не использует role-checks — workspace без
# owner'a работает; документировать здесь, чтобы будущий рефактор не словил.
session.add(WorkspaceMember(workspace_id=ws.id, user_id=accepting_user.id, role="member"))
invite.status = "accepted"
invite.accepted_by_user_id = accepting_user.id
invite.accepted_at = now
return workspace
```

**MF14-2 — `create_invite` defence-in-depth assert (как raise, не assert):**
```python
async def create_invite(...):
    # Defence-in-depth: роутер уже проверяет kind='shared' → 403, но если
    # сервис вызовут напрямую из CLI/будущего рефактора — invite на personal
    # CASCADE-снёс бы audit-цепочку (workspace_invites ondelete='CASCADE').
    if workspace.kind != "shared":
        raise ValueError(f"create_invite on non-shared workspace: kind={workspace.kind!r}")
    ...
```

`InviteError` — domain exception, маппится в HTTPException в роутере.

Constants: `INVITE_TTL_DAYS = 7`, `SHARED_WORKSPACE_CAP = 2` (членов в shared),
`SHARED_PER_USER_CAP = 3` (макс shared workspace на юзера — защита workspace
switcher'a в Меню от 100+ items; MF14-4).

### 7.C — Routers + integration

#### `routers/invites.py` (новый)

5 endpoints:
1. **POST `/api/workspaces/{id}/invites`** — `registered_user`. 403 если
   `workspace_id != current_workspace.id` (URL-mismatch защита от escalation),
   403 если `kind != 'shared'`.
2. **GET `/api/workspaces/{id}/invites`** — список invites workspace.
3. **DELETE `/api/workspaces/{id}/invites/{invite_id}`** — revoke (pending→revoked).
   409 если уже terminal.
4. **GET `/api/invites/{token}`** — preview для accept screen (`active_user`,
   не требует membership — token = авторизация). Возвращает workspace_name,
   inviter_display_name (с fallback на first_name).
5. **POST `/api/invites/{token}/accept`** — `registered_user`. После accept:
   `user.active_workspace_id = ws.id` (PIN-B: deep-link → юзер ожидает попасть
   в shared сразу). Возвращает `InviteOut` со status='accepted'.

#### `routers/workspaces.py` — POST shared (расширить)

```python
class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    # kind НЕ принимаем — этот endpoint ВСЕГДА shared; personal только через
    # provisioning, без пути создать второй personal.

@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_shared_workspace(
    body: WorkspaceCreate, user: User = Depends(registered_user), session = ...
):
    ws = Workspace(name=body.name, kind="shared")
    session.add(ws); await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await session.commit()
    return ws
```

PATCH/DELETE workspaces — НЕ добавляем (rename + delete — backlog).

#### `routers/audit.py` (новый)

```python
@router.get("/audit", response_model=list[AuditEntryOut])
async def list_audit(ws = Depends(current_workspace), session = ..., limit: int = Query(50, ge=1, le=200)):
    if ws.kind != "shared":
        return []  # personal не показывает (200 пустой стабилен; UI скрывает раздел)
    # LEFT JOIN на users для actor_display_name; fallback на snapshot_json[actor_name_snapshot] если NULL.
    stmt = (
        select(AuditLog, User.display_name, User.first_name)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(AuditLog.workspace_id == ws.id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    ...
```

Schema `AuditEntryOut`: id, actor_display_name (with fallback), entity_type,
entity_id, action, created_at.

#### Integration в `transactions.py` / `accounts.py` / `planned.py`

**Заполнение `created_by_user_id` + audit-вызовы в каждом CRUD:**

```python
# transactions.create_transaction (current_user уже Depend):
tx = Transaction(workspace_id=ws.id, created_by_user_id=user.id, **body.model_dump(exclude_none=True))
session.add(tx)
if body.kind == "income":
    await skim_on_income(session, tx, actor_user_id=user.id)
await session.flush()  # tx.id для audit
await log_action(session, workspace_id=ws.id, actor=user,
                 entity_type="transaction", entity_id=tx.id, action="create", entity=tx)
try: await session.commit()
except IntegrityError as e: ...  # existing handler

# transactions.update_transaction (добавить current_user Depend):
... setattr ...
await log_action(..., action="update", entity=tx)
await session.commit()

# transactions.delete_transaction (добавить current_user Depend; уже есть planned-guard MF11-2):
await log_action(..., action="delete", entity=tx)
await session.delete(tx); await session.commit()

# accounts.{create,update}_account — analogous (current_user добавить).
# planned.{create,update,delete}_planned, planned.confirm — created_by_user_id заполнить
#   (audit не нужен — entity_type='planned_operation' не в schema; backlog).
# planned.confirm — tx_payload["created_by_user_id"] = user.id; actor = тот, кто confirm нажал.
```

#### `main.py` — include `invites`, `audit` routers.

### 7.D — me-router (registration / soft-delete / restore) + auth-deps

#### `auth/deps.py` расширение

```python
async def current_user(...) -> User:  # existing — без изменений
async def active_user(user: User = Depends(current_user)) -> User:
    """Не soft-deleted. /me/restore depend'ит от current_user напрямую."""
    if user.deleted_at is not None:
        raise HTTPException(410, "account soft-deleted; call /api/me/restore")
    return user

async def current_workspace(user: User = Depends(active_user), session=...) -> Workspace:
    # existing logic, но теперь зависим от active_user (не current_user)
    ...

async def registered_user(user: User = Depends(active_user)) -> User:
    if user.display_name is None or user.consent_at is None:
        raise HTTPException(412, "registration required (display_name, consent)")
    return user
```

**Иерархия:**
```
tg_user_from_auth → current_user → active_user → current_workspace
                                              ↘  registered_user
```

`/me/restore` uses `current_user` напрямую (иначе круговая 410).

#### `schemas/user.py` — расширить

```python
class MeOut(BaseModel):
    """MF14-6: НЕ `from_attributes=True` — конструируем explicit в роутере
    (`build_me_out` helper ниже), иначе SQLAlchemy подтянет `User.id` (ORM id)
    в поле `id` и сломает фронт, который читает `me.id` как tg_id.

    Поле `internal_id` (renamed from `user_id`) — внутренний ORM id для
    «это я»-маркера в audit UI. НИКОГДА не отправляется на /api/users/{id}
    (такого endpoint'a нет; защита от accidental escalation).
    """
    id: int                          # tg_id (для совместимости с frontend body.id)
    first_name: str
    last_name: str | None
    username: str | None
    language_code: str | None
    is_premium: bool | None
    photo_url: str | None
    internal_id: int                 # ORM User.id (для audit UI «это я»)
    active_workspace_id: int | None
    display_name: str | None
    email: str | None
    consent_at: datetime | None
    deleted_at: datetime | None
    registration_required: bool      # True если display_name OR consent_at NULL

class RegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    # MF14-5: Literal[True] — Pydantic отбивает False автоматом → 422.
    # Альтернатива `consent: bool` с runtime check уязвима к PYTHONOPTIMIZE=1
    # если кто-то напишет `assert body.consent`.
    consent: Literal[True]
```

#### `routers/me.py` расширить

**C14-5 — Явное MeOut construction (НЕ `from_attributes=True`):**
```python
def _build_me_out(user: User, tg_user: TelegramUser) -> MeOut:
    return MeOut(
        id=tg_user.id,                       # tg_id из initData (frontend читает .id)
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
        username=tg_user.username,
        language_code=tg_user.language_code,
        is_premium=tg_user.is_premium,
        photo_url=tg_user.photo_url,
        internal_id=user.id,                  # MF14-6: ORM id (renamed)
        active_workspace_id=user.active_workspace_id,
        display_name=user.display_name,
        email=user.email,
        consent_at=user.consent_at,
        deleted_at=user.deleted_at,
        registration_required=(
            user.display_name is None or user.consent_at is None
        ),
    )
```

- **GET `/me`** — provisioning + `MeOut` (включая `registration_required` derived).
- **POST `/me/register`** — `current_user`. `consent=False → 422`. Идемпотентно.
- **POST `/me/delete`** — `current_user`. Ставит `deleted_at=now`, `active_workspace_id=None`,
  архивирует все personal workspaces юзера (`Workspace.kind='personal'`, через
  membership join), убирает membership из shared (`DELETE FROM workspace_members
  WHERE user_id=user.id AND workspace_id IN (SELECT id FROM workspaces WHERE
  kind='shared')`). Shared workspace и его данные остаются (PIN-G: B продолжает
  использовать shared без A). Идемпотентно (повторный → no-op).
- **POST `/me/restore`** — `current_user`. Если `deleted_at IS NULL` → no-op.
  Если `(now - deleted_at).days >= 30` → 410 «past restore window». Иначе:
  `deleted_at=None`, **un-archive ALL personal workspaces юзера** (MF14-3),
  `active_workspace_id` ← первый из них (invariant: provisioning создаёт ровно
  один personal на юзера). **Не восстанавливает membership в shared** (PIN-G).

Константа `PURGE_AFTER_DAYS = 30` в `app/config.py` или новом `app/services/users.py`.

#### `services/purge.py` (новый) + CLI

```python
async def hard_purge_user(session: AsyncSession, user: User) -> None:
    """Purge порядок (ADR-0009 §8, §7.5):
      0. GUARD (MF14-7): raise ValueError если user.deleted_at IS NULL OR
         (now - deleted_at).days < PURGE_AFTER_DAYS. Защита от случайного
         CLI-запуска на живом или преждевременно-deleted юзере. raise (не
         assert) — устойчиво к PYTHONOPTIMIZE=1.
      1. envelope_entries WHERE workspace_id IN personal_ws
      2. transactions WHERE workspace_id IN personal_ws
      3. planned_operations WHERE workspace_id IN personal_ws
      4. accounts/categories/budgets/envelopes WHERE workspace_id IN personal_ws
      5. audit_log WHERE workspace_id IN personal_ws
      6. workspace_invites WHERE workspace_id IN personal_ws (CASCADE по workspace
         сработал бы, но делаем явно для контроля)
      7. workspace_members WHERE user_id=user.id (вкл. остатки в shared)
      8. workspaces WHERE id IN personal_ws_ids (только personal — shared не трогаем)
      9. user
    Shared workspace'ы НЕ удаляются: actor_user_id/created_by_user_id в shared
    данных каскадно SET NULL после шага 9 через FK.
    """
```

**CLI v1:** `python -m app.cli purge-deleted-users` — manual maintenance trigger
(cron-фреймворк — backlog, юзеров 2).

#### `pyproject.toml` — добавить `email-validator>=2.2` (для `pydantic.EmailStr`).

### 7.E — Frontend (start_param + RegistrationPage + InviteAcceptPage + RestorePage + WorkspaceSwitcher + AuditHistoryPage)

#### `init.ts` — start_param чтение

После `initData.restore()`:
```typescript
const launchParams = retrieveLaunchParams();
const startParam = launchParams.tgWebAppStartParam;
if (startParam?.startsWith('invite_')) {
  sessionStorage.setItem('pendingInviteToken', startParam.slice('invite_'.length));
} else {
  // MF14-8: открыли Mini App без invite-link → стереть stale pending,
  // иначе loop'ит редирект на /invites/.../accept каждое открытие
  // до закрытия TG WebView.
  sessionStorage.removeItem('pendingInviteToken');
}
```

**Также:** в `InviteAcceptPage` на терминальные 410 (expired) / 409
(cap_reached/already_accepted/already_member) — `sessionStorage.removeItem(
'pendingInviteToken')`, чтобы юзер не loop'ил после ошибки.

**C14-6 + C15-3 — SDK migration note:** при migration на
`@telegram-apps/sdk-react@3.3+` (sprint 4 backlog) проверить:
- `retrieveLaunchParams()` возвращает объект с полем `tgWebAppStartParam`
  ИЛИ переименованным `startParam` (3.x naming).
- Если переименован — fallback:
  `const startParam = lp.tgWebAppStartParam ?? lp.startParam;`
- ADR-0003 не затрагивается (initData.raw HMAC независим от naming).

#### `components/App.tsx` — redirect logic

```typescript
useEffect(() => {
  if (!me.user) return;
  // Priority: soft-deleted → restore. Иначе registration. Иначе pending invite.
  if (me.user.deleted_at && pathname !== '/restore') navigate('/restore');
  else if (me.user.registration_required && pathname !== '/register') navigate('/register');
  else {
    const pending = sessionStorage.getItem('pendingInviteToken');
    if (pending) {
      sessionStorage.removeItem('pendingInviteToken');
      navigate(`/invites/${pending}/accept`);
    }
  }
}, [me.user, pathname]);
```

#### `hooks/useMe.ts` — типы `MeOut` (расширить).

#### Новые страницы:

- **`RegistrationPage`** (`/register`) — form display_name (required), email
  (optional), consent checkbox. POST `/me/register` → success → **C16-3:** НЕ
  делаем `navigate('/')` сами; только `refetch` me. App.tsx useEffect увидит
  обновлённый `registration_required=false` → разрулит цель (pending invite
  или `/`). Single navigate вместо двух последовательных, без flicker'a главной.
  **C17-1:** MainButton `disabled={submitting}` во время in-flight POST; на
  success cleared, App.tsx редиректит на следующем render'е. Без явного
  submitting-state юзер не понимает, обработался ли клик за время latency POST.
- **`RestorePage`** (`/restore`) — текст + кнопка POST `/me/restore`.
- **`InviteAcceptPage`** (`/invites/:token/accept`) — GET `/invites/{token}` →
  preview → MainButton «Принять» → POST `/invites/{token}/accept` → 200 →
  navigate `/` (active workspace уже переключён). Маппинг ошибок: 410 expired
  → «срок истёк», 409 cap_reached → «макс участников», 409 already_member →
  редирект на `/`. MainButton ref-pattern (P3 stale-closure ловушка).
- **`AddSharedWorkspacePage`** (`/workspaces/new`) — input name → POST `/workspaces`
  (kind='shared' implicit).
- **`AuditHistoryPage`** (`/audit`) — список последних 50 записей audit. Раздел
  скрыт в MenuPage если `currentWorkspace.kind === 'personal'`.
  - Каждая запись отображает `actor_display_name` + relative time + entity
    label («транзакция», «счёт») + action («создал», «изменил», «удалил»).
  - **MF15-3 (renamed):** если `audit.actor_user_id === me.internal_id` →
    бейдж «вы». Fallback на `snapshot_json.actor_name_snapshot` если
    `actor_user_id IS NULL` (юзер hard-purged) → подпись «бывший участник».
    Это единственное место, где `internal_id` используется на frontend —
    server-side never accept'ит его в URL/body (защита от accidental
    escalation).

#### `MenuPage.tsx` — расширить:

```tsx
<Section header="Рабочая область">
  {workspaces.map(ws => (
    <Cell key={ws.id} after={ws.id === active ? '✓' : null}
          subtitle={ws.kind === 'shared' ? 'Совместный' : 'Личный'}
          onClick={() => switchWorkspace(ws.id)}>{ws.name}</Cell>
  ))}
  <Cell onClick={() => navigate('/workspaces/new')}>+ Создать совместную</Cell>
</Section>
<Section header="Управление">
  {currentWs.kind === 'shared' && <Cell onClick={() => navigate('/audit')}>История изменений</Cell>}
  <Cell onClick={() => navigate('/envelopes')}>Конверты</Cell>  {/* existing */}
  <Cell onClick={() => navigate('/me/delete-confirm')}>Удалить аккаунт</Cell>
</Section>
```

`switchWorkspace(id)` — PATCH `/api/workspaces/active` → refetch.

#### Новые хуки:

- `useWorkspaces` — GET `/api/workspaces`.
- `useInvites(workspaceId)` — GET `/api/workspaces/{id}/invites`.
- `useAudit(limit)` — GET `/api/audit?limit=50`.

PIN-C frontend (PIN-F UX из P6 опыт): 409/410 на invite-accept подаются как
мягкие сообщения, не destructive — «срок истёк / уже принято / макс участников».

---

## 3. Зависимости

- **`email-validator>=2.2`** — `pydantic.EmailStr`. Под MIT, стабильная.
- **Frontend:** новых npm-пакетов нет (`retrieveLaunchParams()` уже в коде).
- **C14-4 — `VITE_BOT_USERNAME` env-переменная** во frontend build для
  построения invite-link `https://t.me/<bot>?startapp=invite_<token>`. Альтернатива
  — отдельный endpoint `GET /api/config` → `{bot_username}`. Решено: env (статика,
  не меняется в runtime). Bot username = `pulse_drill_bot` (см. CLAUDE.local.md).

---

## 4. Open questions / PIN'ы (для reviewer pass-cycle)

- **PIN-A (cap-race второго порядка):** `SELECT FROM workspaces ... FOR UPDATE`
  на workspace ПЕРЕД lock'ом invite — сериализует все concurrent accept'ы.
  Альтернатива: денормализованная `member_count` колонка + CHECK + триггер
  (overkill для cap=2). Тест: `test_concurrent_accept_two_invites_one_workspace_caps_at_2`
  по образцу `TestConcurrencyConfirm` (P5).
- **PIN-B (auto-switch active_workspace_id после accept):** ставим (deep-link →
  юзер ожидает попасть в shared). Альтернатива: оставить, toast «Accepted, switch?»
  — лишний кулик.
- **PIN-C (soft-deleted на GET /me):** не auto-restore. Возвращаем MeOut с
  deleted_at → фронт редиректит на /restore.
- **PIN-D (audit для personal):** пишем всегда; UI скрывает в personal. Альтернатива
  «писать только для shared» усложняет log_action (ws.kind check) без экономии.
- **PIN-E (registration enforce):** только sharing-операции (decision approved).
- **PIN-F (`active_user` vs `current_user`):** `current_user`=есть в БД;
  `active_user`=не soft-deleted; `registered_user`=display_name+consent. Чётко
  расслоено, /me/restore depend'ит от `current_user` напрямую (избежать круговой).
- **PIN-G (restore не восстанавливает shared membership):** decision approved.
  Тест явно фиксирует поведение.
- **PIN-H (registration optional на личных POST):** не блокируем (decision E
  approved). Не делаем backfill `display_name = first_name` — first_name из TG
  достаточно для всех UI кроме compliance (где force консент).
- **PIN-I (audit только tx/accounts):** envelopes/planned/categories — backlog.
- **PIN-J (audit after-state vs diff):** after-state. Diff/jsonpatch — backlog.
- **PIN-K (TG SDK API):** `retrieveLaunchParams().tgWebAppStartParam` (паттерн
  из `index.tsx:22`).
- **PIN-L (cron для hard-purge):** manual CLI v1. Cron — backlog.
- **PIN-M (shared без members):** A soft-delete + B уже вышел → shared dangling
  до hard-purge. Не достижимо в реальности для двоих. Backlog: auto-archive shared
  без members.
- **PIN-N (cap shared-per-user, MF14-4):** `SHARED_PER_USER_CAP = 3` — макс
  shared workspace на юзера (1 personal + 3 shared = 4 строки в switcher,
  помещается). Enforce в `accept_invite` после workspace lock. Тест
  `test_accept_fourth_shared_returns_409`.
- **PIN-O (audit retention):** v1 — **forever** (явно). Backlog: 1-year sliding
  window когда appears производственная нагрузка. Размер (C15-4 корректировка):
  один row ≈ 200-400 байт (snapshot_json), ~50 rows / 3 дня dogfood'a
  ≈ **150 KB / месяц** (не 6 KB как было). За 5 лет: ~9 MB — пренебрежимо.
- **PIN-P (inviter soft-deletes after invite creation, before accept):** invite
  валиден (workspace_id остался, FK CASCADE не сработал — workspace_invites.
  created_by_user_id SET NULL); B принимает, становится единственным участником
  shared. **Это OK по семантике** «B продолжает использовать shared без A»
  (ADR-0009 §5). Тест `test_accept_after_inviter_soft_deletes`.

---

## 5. Что НЕ делаем (backlog)

- 3+ участников / roles admin/member.
- PATCH /workspaces/{id} (rename).
- Cron hard-purge (manual CLI v1).
- Audit на envelopes/planned/categories.
- Audit diff/jsonpatch.
- Email-verification flow.
- Email-recovery аккаунта.
- Push-уведомления.
- TG bot share-link UI (генерация share-message с inline button) — v1 frontend
  копирует https://t.me/<bot>?startapp=invite_<token> в clipboard.
- Auto-restore shared membership (PIN-G).
- `workspaces.member_count` денормализация.
- audit_log partitioning.

---

## 6. Тесты (~50 новых)

**По образцу P5/P6 драфтов; полный список:**

- `test_workspace_invites.py` (~18 тестов): POST happy + cross-ws 403 + personal-ws
  403 + non-registered 412; GET list; DELETE revoke pending/409 accepted; GET
  preview + 404; POST accept happy + already-accepted 409 + expired 410 + revoked
  409 + already-member 409 + cap-2 409; **`test_concurrent_accept_same_token`**
  (2 sessionmaker, asyncio.gather → один 200, один 409 через FOR UPDATE на invite);
  **`test_concurrent_accept_two_invites_one_workspace_caps_at_2`** (PIN-A: FOR UPDATE
  на workspace); soft-deleted user accept → 410;
  **`test_accept_fourth_shared_returns_409`** (PIN-N cap_shared_per_user=3);
  **`test_accept_after_inviter_soft_deletes`** (PIN-P: B становится единственным
  участником, audit показывает «бывший участник»);
  **`test_concurrent_accept_two_different_workspaces_same_user_caps_at_3`**
  (MF15-1: advisory_xact_lock(user.id) сериализует cross-workspace accept'ы
  одного юзера; 4-й параллельный → один 200, один 409 user_cap_reached).
- `test_workspaces_create_shared.py` (~3): POST happy; extra=forbid kind → 422;
  without registration → 412.
- `test_audit_log.py` (~8): POST tx → 1 строка audit; PATCH/DELETE tx → строки;
  POST/PATCH account → строки; audit.workspace_id = ws.id (не personal A);
  cross-workspace оба B+A видят строки в shared; GET /audit в personal → пустой;
  actor_user_id after hard-purge → NULL.
- `test_me_register.py` (~6): registration_required True для нового; POST register
  happy + state update; consent=False → 422; invalid email → 422;
  **`test_me_out_helper_covers_all_fields`** (MF15-2 canary, MF16-1 fix):
  ```python
  built = _build_me_out(fake_user, fake_tg_user)
  explicit = set(built.model_dump(exclude_unset=True).keys())
  assert explicit == set(MeOut.model_fields.keys()), \
      f"missing: {set(MeOut.model_fields) - explicit}"
  ```
  `exclude_unset=True` критичен: без него Pydantic v2 дампит все поля с
  defaults, тест зелёный даже когда helper забыл передать новое поле →
  прод вернёт None вместо реального значения (тот класс drift'a, против
  которого MF14-6 введён).
- `test_me_soft_delete.py` (~6): POST /me/delete → state; shared workspace
  survives; POST /me/restore happy в окне; POST /me/restore вне окна (mock
  deleted_at=-31d) → 410; restore без deleted_at no-op; **restore не восстанавливает
  shared membership** (PIN-G).
- `test_me_dependencies.py` (~3): `active_user` блокирует soft-deleted (любой POST
  → 410); `registered_user` блокирует non-registered → 412; `current_user` пропускает
  soft-deleted на GET /me.
- `test_created_by_user_id.py` (~5): tx/account/planned created → assignment user.id;
  confirm planned → tx.created_by_user_id = user.id (actor confirm, не plan creator);
  после hard-purge → SET NULL.
- `test_hard_purge_user.py` (~7): full purge personal data; shared survives; audit
  actor_user_id → NULL; invite created_by → NULL; tg_id освобождён → новый /me
  создаёт нового User; **C16-2 boundary:**
  `test_hard_purge_user_at_exactly_30_days_passes_guard` (deleted_at=now-30d →
  ok), `test_hard_purge_user_at_29_days_raises_value_error` (граничный регресс-
  защитник для `>= PURGE_AFTER_DAYS`).
- `test_migration_0006.py` smoke: upgrade head + downgrade с 4 guards.
- `test_invite_preview.py` (~2): preview shows workspace_name/inviter_display_name;
  fallback на first_name если display_name NULL.

Frontend автотесты — не пишем (паттерн P5/P6).

---

## 7. Готовность к старту (последовательность имплементации)

После reviewer pass-cycle (passes 14+, ожидаемо 2-3 прохода — сходимость уже
быстрее P5/P6 благодаря паттернам track record) + применения must-fix:

**7.A** (1 сессия): pre-Phase 7 refactor (current_user в недостающих endpoint'ах)
+ модели Envelope... извините, WorkspaceInvite/AuditLog + User расширение +
миграция 0006 + conftest TRUNCATE + test_migration_0006 smoke + test_migration_0005
explicit revisions. Закрытие — `pyproject.toml` `email-validator`.

**7.B** (короткая сессия): `services/audit_log.py` + `services/invites.py`
с FOR UPDATE на workspace для cap-race protection. ~10 unit-тестов на сервисы
(`test_audit_log_service.py`, `test_invites_service.py`).

**7.C** (большая сессия): `routers/invites.py` (5 endpoint'ов), POST
`/workspaces` shared, `routers/audit.py` GET, integration в transactions/
accounts/planned (заполнение `created_by_user_id` + log_action вызовы).
~25 тестов (workspace_invites + audit_log + created_by_user_id + ratis).
`main.py` includes.

**7.D** (средняя сессия): `auth/deps.py` (active_user, registered_user,
current_workspace через active_user) + `routers/me.py` расширение (register/
delete/restore + MeOut) + `services/purge.py` + manual CLI. ~15 тестов
(me_register + me_soft_delete + me_dependencies + hard_purge).

**7.E** (большая сессия): frontend — start_param в init.ts + App.tsx redirect
logic + 5 новых страниц + 3 новых хука + MenuPage расширение.

**Deploy:**
- Backend 7.A-D через `./deploy.sh` (migrate-smoke ловит 0006).
- Frontend 7.E через `SKIP_MIGRATE_SMOKE=1 ./deploy.sh`.

**PAUSE 7:**
- A создаёт shared «Семейный», копирует invite-link (https://t.me/<bot>?startapp=invite_<token>),
  шлёт B вручную в TG.
- B открывает, видит preview, MainButton «Принять» → попадает в shared.
- A заносит «Зарплата 100к», B видит после refresh.
- B заносит расход, A видит, audit показывает «B: Расход 500₽ только что».
- A переключается в Личный → видит только своё.
- A soft-delete → warning «Личные данные удалятся через 30 дней, общие останутся
  у партнёра» → подтверждает.
- A открывает Mini App → /restore screen → восстанавливает → видит личный
  workspace; shared не вернулся (PIN-G).

---

## 8. Critical Files

**Backend:**
- `backend/app/auth/deps.py` — добавить `active_user`, `registered_user`;
  `current_workspace` через `active_user`.
- `backend/app/routers/me.py` — расширить (MeOut, register/delete/restore).
- `backend/app/routers/invites.py` — НОВЫЙ (5 endpoints).
- `backend/app/routers/audit.py` — НОВЫЙ (GET /audit).
- `backend/app/routers/workspaces.py` — POST shared.
- `backend/app/routers/transactions.py` — заполнение `created_by_user_id` +
  log_action в create/update/delete (паттерн).
- `backend/app/routers/accounts.py` — аналогично.
- `backend/app/routers/planned.py` — `created_by_user_id` в create + confirm
  (audit на planned — backlog).
- `backend/app/services/audit_log.py` — НОВЫЙ.
- `backend/app/services/invites.py` — НОВЫЙ.
- `backend/app/services/purge.py` — НОВЫЙ + CLI entrypoint.
- `backend/app/models/audit_log.py`, `backend/app/models/workspace_invite.py` — НОВЫЕ.
- `backend/app/models/user.py` — добавить 4 nullable колонки.
- `backend/app/models/__init__.py` — добавить imports.
- `backend/alembic/versions/0006_sharing_and_audit.py` — НОВАЯ миграция.
- `backend/app/schemas/user.py` — MeOut, RegistrationBody.
- `backend/app/main.py` — include `invites`, `audit`.
- `backend/pyproject.toml` — `email-validator>=2.2`.

**Frontend:**
- `frontend/src/init.ts` — start_param → sessionStorage.
- `frontend/src/components/App.tsx` — redirect logic.
- `frontend/src/hooks/useMe.ts` — типы MeOut.
- `frontend/src/hooks/useWorkspaces.ts` — НОВЫЙ.
- `frontend/src/hooks/useInvites.ts` — НОВЫЙ.
- `frontend/src/hooks/useAudit.ts` — НОВЫЙ.
- `frontend/src/pages/RegistrationPage/RegistrationPage.tsx` — НОВАЯ.
- `frontend/src/pages/RestorePage/RestorePage.tsx` — НОВАЯ.
- `frontend/src/pages/InviteAcceptPage/InviteAcceptPage.tsx` — НОВАЯ.
- `frontend/src/pages/AddSharedWorkspacePage/AddSharedWorkspacePage.tsx` — НОВАЯ.
- `frontend/src/pages/AuditHistoryPage/AuditHistoryPage.tsx` — НОВАЯ.
- `frontend/src/pages/MenuPage/MenuPage.tsx` — расширить.
- `frontend/src/navigation/routes.tsx` — добавить routes.
- `frontend/src/lib/refetch.ts` — +`'workspaces'`, +`'invites'`, +`'audit'` ключи.

---

## 9. Verification (end-to-end)

**После 7.A backend deploy:**
- `ssh pulse-drill 'docker compose exec -T db psql -U pulse -d pulse -tA -c "
  SELECT version_num FROM alembic_version;
  SELECT to_regclass('"'"'workspace_invites'"'"');
  SELECT to_regclass('"'"'audit_log'"'"');
  SELECT column_name FROM information_schema.columns
    WHERE table_name='"'"'users'"'"' AND column_name IN ('"'"'display_name'"'"', '"'"'email'"'"', '"'"'consent_at'"'"', '"'"'deleted_at'"'"');
  "'`
- Все 4 новые users-колонки + 2 новых таблицы должны быть.

**После 7.C backend deploy:**
- Через curl с tma initData: `curl -H "Authorization: tma <raw>" https://<host>/api/audit` → пустой.
- POST tx через UI → `psql -c "SELECT count(*) FROM audit_log"` → 1.
- POST shared workspace → POST invite → видим в БД `workspace_invites` запись.

**После 7.D backend deploy:**
- `GET /api/me` без registration → `MeOut.registration_required = true`.
- POST `/api/me/register` без display_name → 422.
- POST `/api/me/register` happy → state update.
- POST `/api/workspaces` без registration → 412.

**После 7.E frontend deploy:**
- Открыть Mini App → новый юзер → авто-redirect на /register → заполнить → попадаем
  на /.
- На Меню → «Создать совместную» → имя → попадаем в shared workspace.
- На Меню → «Управление» → копируем invite-link (формат:
  `https://t.me/<bot>?startapp=invite_<token>`).
- Открыть линк на втором TG аккаунте → видим preview → принимаем → попадаем в
  shared.
- На / шарингового workspace оба видят tx.
- На Меню → «История изменений» (только в shared) → видим audit с actor.
- Меню → «Удалить аккаунт» → подтверждаем → следующий /me → /restore screen →
  восстанавливаем → personal вернулся.

**Pytest suite:**
- 194 текущих + ~50 новых тестов P7 → ~244 passed.
- Flaky `TestConcurrencyConfirm` остаётся known issue (test isolation на shared
  engine; не блокирует).

**migrate-smoke на копии prod:**
- `infra/scripts/migrate-smoke.sh` прокатывает 0006 на копии.
- Assertion'ы расширить (по аналогии с 0003 smoke): новые таблицы существуют,
  users.deleted_at nullable, нет orphan invites/audit. Опционально — backfill
  smoke не нужен (всё nullable).
