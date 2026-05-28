# ADR 0009 — Workspace-scoping для multi-user (совместный аккаунт)

- **Status:** Proposed (ждёт plan-reviewer — фокус ревью сюда)
- **Date:** 2026-05-28
- **Deciders:** @orrin

## Контекст

Dogfood вдвоём показал: нужен совместный учёт. Сейчас владение всеми ресурсами
键ится на `user_id` напрямую. Требования (architecture #2 фидбека): совместный
«аккаунт» как отдельная выбираемая сущность; приглашение изнутри приложения; оба
участника root; история изменений (кто/что/когда); **без каскадного удаления**
(удалил один — у второго общие данные остаются); v1 максимум 2 участника.

## Рассмотренные варианты

### `shared_account_group_id` на `accounts`

- Узко: шарить надо и транзакции, и категории, и конверты, и планы — не только счета.
- group_id, размазанный по всем таблицам, неоднозначен; легко словить cross-tenant утечку.

### Workspace + membership (единый scope-ключ) ✅ выбрано

`workspaces(id, name, kind('personal'|'shared'), created_at)` +
`workspace_members(workspace_id, user_id, role, joined_at, unique(workspace_id,user_id))`.
**Re-key** всех владеемых таблиц `user_id → workspace_id`. Юзер «работает от лица»
выбранного workspace (`users.active_workspace_id`).

Имя **workspace**, не «household»/«account» — чтобы не коллидить с финансовым «счёт/account».

## Решение

1. **Re-key.** Phase 4 мигрирует **только 6 существующих** таблиц
   (`accounts, transactions, categories, budgets, goals, receipts`): `user_id →
   workspace_id`. `planned_operations` (P5) и `envelopes`/`envelope_entries` (P6)
   **рождаются workspace-native**, не мигрируются повторно (consistency — это выгода
   foundation-first). Системные категории: `user_id IS NULL` → `workspace_id IS NULL`
   (глобальные). Partial-unique → `COALESCE(workspace_id,0)`.
2. **Порядок миграции и FK (must-fix #1):**
   - Старые `<table>_user_id_fkey` все `ON DELETE CASCADE` → **дропнуть ДО backfill**:
     пока колонка жива, удаление юзера каскадом снесёт уже расшаренные строки.
   - Новый FK `workspace_id → workspaces.id` **БЕЗ cascade** (`RESTRICT`/`NO ACTION`):
     workspace с данными нельзя удалить мимо архивации — это и держит «без каскада».
   - `workspace_members.user_id → ON DELETE CASCADE` (удаление членства безопасно),
     `workspace_members.workspace_id → ON DELETE CASCADE`.
   - `users.active_workspace_id → ON DELETE SET NULL` (удаление workspace не валит юзера).
3. **Провизионинг:** каждому юзеру personal workspace (`role='owner'`) + 2 дефолтных
   счёта в нём. `users.active_workspace_id` = personal на старте.
4. **Auth (два слоя, must-fix #8):** `current_workspace` **ре-валидирует membership на
   каждом запросе** (не доверяет сохранённому id) → не член → 403. Switch-PATCH
   `active_workspace_id` **проверяет membership ДО записи** (иначе юзер выставит чужой
   workspace и `WHERE workspace_id=...` пройдёт). Все запросы `WHERE workspace_id =
   current_workspace.id`; cross-resource → 404 (cross-tenant guard из `transactions.py`).
5. **Без каскадного удаления:** удаление юзера убирает строки `workspace_members`, но НЕ
   shared workspace (键ится на `workspace_id`, FK без cascade — см. п.2). Personal
   workspace архивируется с юзером. Shared-данные переживают, т.к. не зависят от `user_id`.
   `workspaces.kind` — **инвариант**: invite-эндпоинт только для `kind='shared'`, personal
   расшарить нельзя (не даём `kind` разойтись с member-count).
6. **Аудит:** `created_by_user_id` (nullable, `ON DELETE SET NULL`) на мутируемых таблицах
   + `audit_log(id, workspace_id, actor_user_id, entity_type, entity_id, action, snapshot_json,
   created_at)` для «кто/что/когда» (v1: transactions + accounts). Колонку
   `created_by_user_id` заводим в P4, заполняем с P7 → строки P4–P6 имеют actor=NULL
   (окно ещё одно-пользовательское, ожидаемо).
7. **Приглашения:** `workspace_invites(id, workspace_id, token, created_by_user_id,
   status('pending'|'accepted'|'revoked'|'expired'), expires_at, accepted_by_user_id?,
   created_at)`. `token = secrets.token_urlsafe(32)` (влезает в Telegram `startapp` ~64),
   `expires_at = +7д`. FK `created_by_user_id`/`accepted_by_user_id` → **`ON DELETE SET
   NULL`** (чтобы purge юзера не блокировался инвайтами). Token → Telegram deep-link →
   accept → membership. **v1: shared ≤ 2** (app-level + guard; 3-й accept → 409).
8. **Hard-purge (Phase 7.5, после 30 дней soft-delete) под `workspace_id RESTRICT`
   (must-fix A3):** RESTRICT не даёт удалить workspace, пока есть владеемые строки.
   Нормальная работа использует только soft-archive (RESTRICT не срабатывает), но purge
   обязан удалять **в порядке зависимостей**: дочерние строки (`envelope_entries`,
   `transactions`, `planned_operations`, `accounts`, `categories`, `budgets`, `envelopes`,
   `audit_log`) → personal `workspace` → `user`. Иначе purge бросит FK-violation.

## Последствия

- **Самая рискованная миграция проекта** — трогает каждую владеемую таблицу; прежние
  cross-tenant баги становятся cross-workspace **утечками данных**. Делается в изоляции
  (Phase 4), отдельно от фич, с тестами изоляции и backfill-теста на снапшоте старой схемы.
  **Бэкап БД до деплоя миграции.**
- Планирование и конверты строятся **после** foundation → сразу workspace-native, без
  повторного re-key (см. пушбэк по сиквенсу в [`../v1.0-plan.md`](../v1.0-plan.md)).
- Регистрация (`display_name/email/consent_at`) и soft-delete (30 дней) — Phase 7
  (architecture #6, #7).
- 3+ участников с ролями/админкой — **backlog** (бизнес-сценарий, не v1).
