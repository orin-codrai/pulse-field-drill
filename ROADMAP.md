# Roadmap

6 спринтов (Sprint 3 расщеплён на 3a/3b — итого 7 фаз). У каждого — конкретный
критерий готовности. Финальный экзамен после спринта 6: снести VPS и развернуть
всё заново за ≤30 минут.

---

## Sprint 1 — Hello World в Telegram ✅ закрыт

- [x] Caddy на cloud.ru VPS, HTTPS-сертификат Let's Encrypt
- [x] DNS, BotFather, `t.me/pulse_drill_bot/app` через Direct Link
- [x] `infra/compose/docker-compose.yml` — Caddy
- [x] `infra/caddy/Caddyfile` — статика на `/`, заглушка `/api`
- [x] `frontend/` — форк `Telegram-Mini-Apps/reactjs-template`

**Exit criteria достигнуты:** бот открывается в Telegram, видна тема.

---

## Sprint 2 — initData валидация ✅ закрыт ⭐ САМАЯ ЦЕННАЯ ФАЗА

- [x] `backend/` структура: `pyproject.toml` (uv), `Dockerfile`, `app/`
- [x] `app/auth/init_data.py` — HMAC-SHA256 валидация (9 тестов)
- [x] `app/auth/deps.py` — FastAPI dependency `current_user`
- [x] `app/routers/me.py` — `GET /api/me`
- [x] `tests/test_init_data.py` — реальный + подделанный initData
- [x] backend-сервис в `docker-compose.yml`, прокси через Caddy на `/api`
- [x] Frontend шлёт `Authorization: tma <initDataRaw>` (см. ADR-0003)

**Exit criteria достигнуты:** «Hello, name (#tg_id)» из Telegram WebApp.

**Почему важно:** код `init_data.py` переедет в Pulse 1-в-1. Это
единственная фаза, где «учебный» код тождественно равен «продакшен» коду.

---

## Sprint 3a — Postgres + core домен ✅ закрыт

См. полный план: [`sprint-3-plan.md`](./sprint-3-plan.md).

- [x] Postgres 16 в `docker-compose.yml`, named volume `pgdata`, healthcheck
- [x] SQLAlchemy 2 + asyncpg + Alembic (async template)
- [x] Полная 7-таблица миграция с CHECK + partial unique indexes
- [x] Сидинг 18 системных категорий
- [x] User provisioning + 2 default accounts (idempotent через `ON CONFLICT` + partial unique index)
- [x] CRUD: `accounts`, `categories`, `transactions` + балансы
- [x] Стабы → потом full body для `goals`, `budgets`, `reports`
- [x] Миграции в Dockerfile CMD, не в lifespan (ADR-0006)
- [x] Cross-resource auth (404 на чужие ID) + system-category protection (403)

**Exit criteria достигнуты:** `curl /api/me` создаёт юзера + 2 счёта;
expense/transfer/adjustment дают корректные балансы; CHECK-ограничения
видны в psql. 65 тестов на момент закрытия 3a.

---

## Sprint 3b — extras + UI (phased) ← АКТИВНЫЙ

См. план: [`/home/orrin/.claude/plans/use-the-plan-reviewer-agent-resilient-conway.md`](../../.claude/plans/use-the-plan-reviewer-agent-resilient-conway.md).

**Phase 1 — backend bodies ✅ закрыт**
- [x] Тела `goals` CRUD + `/goals/{id}/progress` (linked → account_balance; unlinked → Σ income из системной «Зарплата»; broken-seed → 500)
- [x] Тела `budgets` CRUD + `/budgets/status` (window: ISO Monday для week, expired excluded)
- [x] `/reports/month` + `/reports/calendar`
- [x] Cross-tenant FK guards (`_validate_*_ref` mirrors из transactions.py)
- [x] IntegrityError mapping (409/422) на POST и PATCH
- [x] 129 тестов всего на момент закрытия Phase 1

**Phase 2 — минимальный frontend для догфуда ✅ закрыт**
- [x] TabBar 3 таба (Балансы / Список / Меню)
- [x] BalancesPage + общий итог + MainButton «+ Транзакция»
- [x] TransactionsPage basic list (без фильтров)
- [x] AddTransactionPage форма (все 4 kind + adjustment direction)
- [x] MainButton ref-pattern (защита от stale closure)
- [x] Event-bus refetch после mutation (lib/refetch.ts)
- [x] Удалить template-страницы (часть Sprint 4 tech-debt)

**🛑 PAUSE для догфуда (2-3 дня)** перед Phase 3.

**Phase 3 — Analytics + filters (~3-4ч)**
- [ ] AnalyticsPage (4-й таб) — month report
- [ ] TransactionsPage filters (kind, period) + «Ещё 50» pagination

**Phase 4 — Menu + manage screens (~4-5ч)**
- [ ] MenuPage с подэкранами
- [ ] GoalsPage + create/edit + progress UI
- [ ] BudgetsPage + status UI + expired section
- [ ] AccountsManagePage
- [ ] CategoriesManagePage

**Exit criteria 3b:** реально пользуюсь со своего телефона. Phase 4 может
быть значительно урезана если goals/budgets окажутся мёртвой фичей по
итогам dogfood'a.

---

## Sprint 4 — Frontend tech-debt (~2–3ч)

- [ ] SDK миграция `@tma.js/sdk-react` → `@telegram-apps/sdk-react@^3.3+` (после research'a v3.3 API; см. post-mortem в CLAUDE.local.md)
- [ ] `tsc --noEmit` снова в `build` script (типы выровнены)
- [ ] Шаблонный мусор: `frontend/.github/`, `frontend/LICENSE`, шаблонный README
- [ ] Обновить `docs/adr/0003-auth-scheme-tma.md` если SDK сменил API contract

**Exit criteria:** frontend на свежем SDK, type-check проходит, шаблонные артефакты убраны.

---

## Sprint 5 — Догфудинг (2 недели, ~0ч кода)

- [ ] Использую каждый день
- [ ] Баги пишу в GitHub Issues
- [ ] **Не чиню сразу.** Цель — реальная приоритезация

**Exit criteria:** список приоритизированных issues, не догадок.

---

## Sprint 6 — Прод-харднинг (~4–5ч)

- [ ] `infra/cron/pg-backup.sh`: `pg_dump | gzip` + ротация 7 дней
- [ ] systemd timer (или sidecar) для запуска бэкапа
- [ ] WireGuard на VPS, SSH только из WG-подсети
- [ ] `.github/workflows/deploy.yml`: push → SSH → `infra/scripts/deploy.sh` (env-check + git pull + compose up; уже существует с Sprint 3a)
- [ ] `GET /api/health` + Caddy health-check
- [ ] Расширить `infra/README.md` до полного runbook «снести VPS и развернуть заново» (стартовый scaffold создан в Sprint 3a)
- [ ] Подумать про шифрование секретов `.env` (sops/age/pass) — сейчас на VPS открытый файл

**Exit criteria:** финальный экзамен — `apt purge docker` (или новый VPS),
восстановление по runbook'у ≤30 минут.

---

## Non-goals (не предлагать ни на каком спринте)

- Мультивалютность, P2P, биржи, крипто
- AI-категоризация трат
- Шеринг, семейные счета
- Сложная аналитика, прогнозы
- Любые фичи, которые не учат инфре или специфике TMA
