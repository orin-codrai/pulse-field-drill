# Pulse

> **Status:** Sprint 1 in progress. Этот README будет переписан, когда
> приложение начнёт работать.

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
| Frontend | React + Vite + `@telegram-apps/sdk-react` (форк `Telegram-Mini-Apps/reactjs-template`) |
| Backend | FastAPI (Python 3.12) |
| Pkg manager | uv |
| ORM / миграции | SQLAlchemy 2.x + Alembic |
| БД | PostgreSQL 16 |
| Bot | aiogram 3.x |
| Reverse proxy | Caddy 2 (автоматический HTTPS) |
| Контейнеризация | docker compose |
| Хостинг | self-hosted Debian VPS |
| VPN | WireGuard (SSH-доступ через приватную сеть) |
| CI/CD | GitHub Actions → SSH deploy |
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
