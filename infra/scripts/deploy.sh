#!/usr/bin/env bash
# deploy.sh — VPS one-command deploy: pull + env-check + compose up + logs.
#
# Запускать из любой dir: скрипт сам находит repo root.
# Не делает rollback / git stash — runbook-уровень, не магия.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

cd "$REPO_ROOT"
echo "== git pull =="
git pull --ff-only

echo ""
echo "== check env =="
"$REPO_ROOT/infra/scripts/check-env.sh" \
    "$REPO_ROOT/.env.example" \
    "$REPO_ROOT/infra/compose/.env"

echo ""
echo "== docker compose up -d --build =="
cd "$REPO_ROOT/infra/compose"
docker compose up -d --build

echo ""
echo "== backend logs (tail 50) =="
docker compose logs --tail=50 backend
