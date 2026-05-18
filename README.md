# Pulse

> **Status:** Sprint 3a in progress (Postgres + core домен). Sprint 1–2 закрыты.
> Mini App работает в Telegram, бэкенд валидирует initData, frontend отдаёт
> `Hello, <name>` через `Authorization: tma <raw>`. Полная дорожная карта —
> в [`ROADMAP.md`](./ROADMAP.md). План текущего спринта — [`sprint-3-plan.md`](./sprint-3-plan.md).

## Что это

`pulse` — личный Telegram Mini App для финансового учёта.

**На самом деле — это полевые учения инфраструктуры.** Проект существует не
ради финтрекера, а чтобы прогнать рукой весь стек, на котором впоследствии
будет построена коммерческая платформа Pulse (AI-ассистент-экосистема для
российских селлеров на WB / Ozon / Яндекс.Маркет).

Финтрекер — низкорисковый учебный кейс. Реальная цель — runbook: **снести
VPS и развернуть всё заново за ≤30 минут**.

## Стек

| Слой | Решение |
|---|---|
| Frontend | React + Vite + `@tma.js/sdk-react@^3.0` (форк `Telegram-Mini-Apps/reactjs-template`; миграция на `@telegram-apps/sdk-react@^3.3+` — Sprint 4, после revert'a в Sprint 2) |
| Backend | FastAPI (Python 3.12), async |
| Pkg manager | uv |
| ORM / миграции | SQLAlchemy 2.x + asyncpg + Alembic (async template) |
| БД | PostgreSQL 16 |
| Bot | aiogram 3.x (планируется) |
| Reverse proxy | Caddy 2 (автоматический HTTPS) |
| Контейнеризация | docker compose |
| Хостинг | self-hosted Debian VPS (cloud.ru) |
| VPN | WireGuard (SSH-доступ через приватную сеть) |
| CI/CD | GitHub Actions → SSH deploy (Sprint 6) |
| Лицензия | **AGPLv3** |

Обоснование выбора стека — в [`docs/adr/`](./docs/adr/).

## Структура репозитория

```
pulse/
├── README.md
├── ROADMAP.md
├── LICENSE              # AGPLv3 (создаётся через `gh repo create`)
├── .env.example         # публичный шаблон секретов
├── backend/             # FastAPI (появится в спринте 2)
├── frontend/            # React + Vite (появится в спринте 1)
├── infra/
│   ├── compose/         # docker-compose.yml
│   ├── caddy/           # Caddyfile
│   └── cron/            # pg-backup.sh (спринт 6)
└── docs/
    └── adr/             # Architecture Decision Records
```

## Локальная разработка

> Появится по мере прохождения спринтов. Пока репо пустой.

## Дорожная карта

См. [`ROADMAP.md`](./ROADMAP.md).

## Лицензия

[AGPL-3.0](./LICENSE) — копилефт, осознанный выбор.
