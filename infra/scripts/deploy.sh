#!/usr/bin/env bash
# deploy.sh — локальный one-command deploy на VPS.
#
# Workflow:
#   1. sync-env.sh: рендерит .env из pass(1) → check-env → scp на VPS.
#   2. Локально: npm run build → frontend/dist (Vite production bundle).
#      На VPS Node не установлен — фронт билдится локально и rsync'ом
#      кладётся в bind-mount Caddy.
#   3. ssh: git pull --ff-only.
#   4. rsync frontend/dist на VPS.
#   5. ssh: docker compose up -d --build + tail logs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_HOST="${PULSE_SSH_HOST:-pulse-drill}"
SKIP_SMOKE="${SKIP_MIGRATE_SMOKE:-0}"

cd "$REPO_ROOT"

echo "== sync .env to VPS =="
"$SCRIPT_DIR/sync-env.sh"

if [ "$SKIP_SMOKE" = "0" ]; then
    echo ""
    echo "== migrate smoke on db copy =="
    # Бэкап + копия prod-БД + alembic upgrade head на копии + ассерты на
    # backfill. Падает — abort до боевой накатки. Бэкап остаётся на VPS как
    # rollback-point. Если миграций в деплое нет (фронт-only) — пропустить
    # через SKIP_MIGRATE_SMOKE=1.
    "$SCRIPT_DIR/migrate-smoke.sh"
else
    echo "(skipping migrate smoke: SKIP_MIGRATE_SMOKE=1)"
fi

echo ""
echo "== npm run build (frontend) =="
# nvm может быть не в PATH non-interactive shell — подсасываем.
if [ -s "$HOME/.nvm/nvm.sh" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.nvm/nvm.sh"
fi
(cd frontend && npm run build)

echo ""
echo "== git pull on $SSH_HOST =="
ssh "$SSH_HOST" '
    set -eu
    cd ~/pulse-field-drill
    git pull --ff-only
'

echo ""
echo "== rsync frontend/dist → VPS =="
rsync -az --delete frontend/dist/ "$SSH_HOST:~/pulse-field-drill/frontend/dist/"

echo ""
echo "== docker compose up -d --build on $SSH_HOST =="
ssh "$SSH_HOST" '
    set -eu
    cd ~/pulse-field-drill/infra/compose
    docker compose up -d --build
    # Caddyfile bind-mounted → docker compose up не перечитывает его, если
    # сам сервис не пересоздан. Reload явно (zero-downtime), чтобы изменения
    # конфига (напр. Cache-Control) применялись на каждом деплое.
    docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
'

echo ""
echo "== backend logs (tail 30) =="
ssh "$SSH_HOST" 'cd ~/pulse-field-drill/infra/compose && docker compose logs --tail=30 backend'

echo ""
echo "OK: deploy finished. Open the Mini App to verify."
