# infra/

Compose-стек, Caddy конфиг, deploy скрипты.

## Layout

```
infra/
├── compose/
│   ├── docker-compose.yml      # стек: db + backend + caddy
│   └── .env                    # ручной, gitignored, заполняется на каждой машине
├── caddy/
│   └── Caddyfile               # reverse proxy + auto-HTTPS
├── scripts/
│   ├── check-env.sh            # сверяет .env c .env.example, печатает diff
│   └── deploy.sh               # git pull + check-env + compose up + logs
└── README.md                   # этот файл
```

`.env.example` — в корне репо. `.env` для compose — в `infra/compose/.env`.

## Deploy на VPS

Однократная настройка:

```bash
git clone <repo> ~/pulse-field-drill
cd ~/pulse-field-drill
cp .env.example infra/compose/.env
# Отредактировать infra/compose/.env: PUBLIC_DOMAIN, TELEGRAM_BOT_TOKEN,
# POSTGRES_PASSWORD (strong, из pass(1) для Pulse — `pulse/db/postgres`).
# DATABASE_URL и POSTGRES_USER/DB обычно как в example.
```

Каждый деплой:

```bash
~/pulse-field-drill/infra/scripts/deploy.sh
```

Скрипт делает `git pull --ff-only` → `check-env.sh` (если в `.env`
не хватает переменных — exit 1 c diff'ом) → `docker compose up -d --build` →
`docker compose logs --tail=50 backend`.

## Что делать, если check-env упал

`check-env.sh` выводит missing переменные и готовые строки для копи-пасты.
Открываем `infra/compose/.env`, добавляем строки, заполняем секреты,
перезапускаем `deploy.sh`.

Stale переменные (есть в `.env`, нет в `.env.example`) выводятся как
WARN — не блокируют деплой, но стоит почистить.

## Локальный smoke без VPS

```bash
cp .env.example infra/compose/.env
# Поставить любые dev-значения (POSTGRES_PASSWORD=devpass и т.д.).
cd infra/compose
docker compose up -d db                  # только postgres, без caddy/backend
docker exec pulse-field-drill-db-1 pg_isready -U pulse -d pulse
```
