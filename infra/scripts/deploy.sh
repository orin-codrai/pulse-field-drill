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

cd "$REPO_ROOT"

echo "== sync .env to VPS =="
"$SCRIPT_DIR/sync-env.sh"

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
'

echo ""
echo "== backend logs (tail 30) =="
ssh "$SSH_HOST" 'cd ~/pulse-field-drill/infra/compose && docker compose logs --tail=30 backend'

echo ""
echo "OK: deploy finished. Open the Mini App to verify."
