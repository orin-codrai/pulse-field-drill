# Roadmap

6 спринтов. У каждого — конкретный критерий готовности. Финальный экзамен
после спринта 6: снести VPS и развернуть всё заново за ≤30 минут.

---

## Sprint 1 — Hello World в Telegram (~3–4ч)

- [ ] `gh repo create pulse --public --license=AGPL-3.0`
- [ ] DNS: A-запись `pulse.<домен>` → VPS
- [ ] `infra/compose/docker-compose.yml` — только Caddy
- [ ] `infra/caddy/Caddyfile` — статика на `/`, заглушка `/api`
- [ ] `frontend/` — форк `Telegram-Mini-Apps/reactjs-template`, собирается в статику
- [ ] BotFather: `pulse_dev_bot`, WebApp URL зарегистрирован

**Exit criteria:** открыть бота в Telegram, нажать кнопку WebApp, увидеть
страницу с применённой темой Telegram.

---

## Sprint 2 — initData валидация (~3–4ч) ⭐ САМАЯ ЦЕННАЯ ФАЗА

- [ ] `backend/` структура: `pyproject.toml` (uv), `Dockerfile`, `app/`
- [ ] `app/auth/init_data.py` — HMAC-SHA256 валидация
- [ ] `app/deps.py` — FastAPI dependency `current_user`
- [ ] `app/routers/me.py` — `GET /me`
- [ ] `tests/test_init_data.py` — реальный + подделанный initData
- [ ] backend-сервис в `docker-compose.yml`, прокси через Caddy на `/api`
- [ ] Frontend шлёт `Authorization: tma <initDataRaw>`

**Exit criteria:**
- `curl -H "Authorization: tma фейк" .../api/me` → **401**
- Открыть из Telegram → **200**, отдаёт `{tg_id, first_name, ...}`

**Почему важно:** код `init_data.py` переедет в Pulse 1-в-1. Это
единственная фаза, где «учебный» код тождественно равен «продакшен» коду.

---

## Sprint 3 — Postgres и схема (~3–4ч)

- [ ] Postgres 16 в `docker-compose.yml`, named volume `pgdata`
- [ ] Alembic настроен, первая миграция
- [ ] Сидинг 13 системных категорий (референс: `cenoff/Money-Bot`)
- [ ] CRUD: `users`, `categories`, `transactions`
- [ ] Endpoints: `GET /categories`, `POST/GET/DELETE /transactions`, `GET /reports/month`

**Exit criteria:** через `curl` создать транзакцию и получить её обратно.
Через `psql` подтвердить данные в БД.

---

## Sprint 4 — UI (~4–5ч)

- [ ] Экран **Добавить**: категория, сумма, заметка, `Telegram.WebApp.MainButton`
- [ ] Экран **Список**: последние транзакции
- [ ] Экран **Отчёт**: итог за месяц по категориям (`/api/reports/month`)
- [ ] Тема: `var(--tg-theme-*)`
- [ ] `useInitData()` из `@telegram-apps/sdk-react`, прокидывается в `fetch`

**Exit criteria:** реально пользуюсь со своего телефона.

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
- [ ] `.github/workflows/deploy.yml`: push → SSH → `git pull` → `docker compose up -d --build`
- [ ] `GET /api/health` + Caddy health-check
- [ ] `infra/README.md` — runbook «снести VPS и развернуть заново»

**Exit criteria:** финальный экзамен — `apt purge docker` (или новый VPS),
восстановление по runbook'у ≤30 минут.

---

## Non-goals (не предлагать ни на каком спринте)

- Мультивалютность, P2P, биржи, крипто
- AI-категоризация трат
- Шеринг, семейные счета
- Сложная аналитика, прогнозы
- Любые фичи, которые не учат инфре или специфике TMA
