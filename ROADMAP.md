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

## Sprint 3a — Postgres + core домен (~8–10ч) ← АКТИВНЫЙ

См. полный план: [`sprint-3-plan.md`](./sprint-3-plan.md).

- [ ] Postgres 16 в `docker-compose.yml`, named volume `pgdata`, healthcheck
- [ ] SQLAlchemy 2 + asyncpg + Alembic (async template)
- [ ] Полная 7-таблица миграция (users, accounts, categories, transactions, goals, budgets, receipts) с ручными CHECK + partial unique indexes
- [ ] Сидинг 18 системных категорий
- [ ] User provisioning + 2 default accounts (idempotent через `ON CONFLICT` + partial unique index)
- [ ] CRUD: `accounts`, `categories`, `transactions` + балансы
- [ ] Стабы (URL + GET 200 + write 501): `goals`, `budgets`, `reports`
- [ ] Миграции в Dockerfile CMD, не в lifespan (ADR-0006)
- [ ] Cross-resource auth (404 на чужие ID) + system-category protection (403)

**Exit criteria:** `curl /api/me` создаёт юзера + 2 счёта; expense/transfer/adjustment
через `curl` дают корректные балансы; `psql \d+ transactions` показывает все
CHECK-constraint-ы текстом.

---

## Sprint 3b — extras + UI (~5–8ч)

- [ ] Тела `goals` CRUD + `/goals/{id}/progress`
- [ ] Тела `budgets` CRUD + `/budgets/status`
- [ ] `/reports/month` + `/reports/calendar`
- [ ] Frontend: экраны **Добавить** / **Список** / **Отчёт**
- [ ] `var(--tg-theme-*)`, MainButton

**Exit criteria:** реально пользуюсь со своего телефона.

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
