#!/usr/bin/env bash
# deploy.sh — локальный one-command deploy на VPS.
#
# Workflow:
#   1. sync-env.sh: рендерит .env из pass(1) → проверяет через check-env.sh →
#      scp на VPS:~/pulse-field-drill/infra/compose/.env.
#   2. ssh: git pull --ff-only + docker compose up -d --build + tail logs.
#
# Запускается ЛОКАЛЬНО (с моей машины). SSH алиас `pulse-drill` должен быть
# настроен в ~/.ssh/config. Секреты должны лежать в pass-store по путям,
# которые читает sync-env.sh (pulse-drill/postgres-password, pulse-drill/bot-token).
#
# Не делает: rollback, health-check ответа, миграций отдельно (миграции в
# Dockerfile CMD migrate-then-serve, см. ADR-0006).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_HOST="${PULSE_SSH_HOST:-pulse-drill}"

cd "$REPO_ROOT"

echo "== sync .env to VPS =="
"$SCRIPT_DIR/sync-env.sh"

echo ""
echo "== git pull + compose up -d --build on $SSH_HOST =="
ssh "$SSH_HOST" '
    set -eu
    cd ~/pulse-field-drill
    git pull --ff-only
    cd infra/compose
    docker compose up -d --build
'

echo ""
echo "== backend logs (tail 30) =="
ssh "$SSH_HOST" 'cd ~/pulse-field-drill/infra/compose && docker compose logs --tail=30 backend'

echo ""
echo "OK: deploy finished. Open the Mini App to verify."
