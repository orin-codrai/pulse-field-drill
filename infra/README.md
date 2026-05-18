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
│   ├── sync-env.sh             # локально: рендерит .env из pass-store, scp на VPS
│   └── deploy.sh               # локально: sync-env → ssh git pull + compose up + logs
└── README.md                   # этот файл
```

`.env.example` — в корне репо. `.env` для compose — в `infra/compose/.env`.

## Deploy на VPS

### Однократная настройка

Локально (на ноутбуке/рабочей машине):

- `pass-store` с двумя записями: `pulse-drill/postgres-password` и
  `pulse-drill/bot-token`. Они читаются `sync-env.sh` в момент деплоя.
- SSH алиас `pulse-drill` в `~/.ssh/config` — указывает на VPS.

На VPS:

```bash
git clone <repo> ~/pulse-field-drill
# .env создастся при первом sync-env.sh. Вручную трогать не нужно.
```

### Каждый деплой

Локально:

```bash
./infra/scripts/deploy.sh
```

Что делает:

1. `sync-env.sh` — рендерит `.env` из `pass-store` (POSTGRES_PASSWORD,
   TELEGRAM_BOT_TOKEN + fixed-значения для остальных), валидирует через
   `check-env.sh`, scp'ит на VPS как `~/pulse-field-drill/infra/compose/.env`
   с правами `chmod 600`.
2. `ssh pulse-drill`: `git pull --ff-only` → `docker compose up -d --build`.
3. `ssh pulse-drill`: tail `docker compose logs backend` — видно как
   alembic накатывает миграции (Dockerfile CMD migrate-then-serve)
   и стартует uvicorn.

`PULSE_SSH_HOST` env-переменная переопределяет SSH-алиас если нужно.

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
