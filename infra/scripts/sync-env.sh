#!/bin/bash
set -euo pipefail

# Resolve repo root relative to this script's location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

LOCAL_TMP=$(mktemp)

# Read secrets once
POSTGRES_PASS=$(pass pulse-drill/postgres-password)
BOT_TOKEN=$(pass pulse-drill/bot-token)

# Render .env to temp file
cat > "$LOCAL_TMP" <<EOF
PUBLIC_DOMAIN=pulse-drill.matreshka-ecommerce.dev
PULSE_ENV=prod
POSTGRES_USER=pulse
POSTGRES_DB=pulse
POSTGRES_PASSWORD=${POSTGRES_PASS}
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
DATABASE_URL=postgresql+asyncpg://pulse:${POSTGRES_PASS}@db:5432/pulse
EOF

echo "Rendered .env with $(wc -l < "$LOCAL_TMP") lines"

# Validate
if ! ./infra/scripts/check-env.sh .env.example "$LOCAL_TMP"; then
  echo "ERROR: rendered .env has missing keys vs .env.example"
  rm -f "$LOCAL_TMP"
  exit 1
fi

# Push to VPS
scp "$LOCAL_TMP" pulse-drill:~/pulse-field-drill/infra/compose/.env
ssh pulse-drill 'chmod 600 ~/pulse-field-drill/infra/compose/.env'

rm -f "$LOCAL_TMP"
echo "OK: .env synced to VPS"
