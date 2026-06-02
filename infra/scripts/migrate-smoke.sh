#!/usr/bin/env bash
# migrate-smoke.sh — прогнать миграцию Alembic на копии prod-БД, до боевой
# накатки. Защита от backfill-багов, которые pytest-smoke не ловит (он на
# пустой БД).
#
# Поток:
#   1. ssh на VPS, source .env (создан sync-env.sh).
#   2. pg_dump pulse → /tmp/pulse-backup-<ts>.sql. Этот файл = ваш rollback.
#   3. DROP/CREATE pulse_migrate_smoke, импорт дампа.
#   4. docker compose build backend — свежий образ с новой миграцией.
#   5. docker compose run --rm с DATABASE_URL → копия → alembic upgrade head.
#   6. SQL-assertions: NOT NULL workspace_id всюду, 18 системных категорий,
#      active_workspace_id у всех юзеров, ровно 1 personal-membership на юзера,
#      POST-счётчики печатаются для сравнения с PRE глазами.
#   7. DROP копии. Дамп оставляем — нужен если боевая накатка упадёт.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_HOST="${PULSE_SSH_HOST:-pulse-drill}"

ssh "$SSH_HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
cd ~/pulse-field-drill/infra/compose

set -a; . ./.env; set +a

TS=$(date +%Y%m%d-%H%M%S)
BACKUP=/tmp/pulse-backup-${TS}.sql
SMOKE_DB=pulse_migrate_smoke

echo "== 1/6 pg_dump $POSTGRES_DB → $BACKUP =="
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --no-owner --no-acl > "$BACKUP"
ls -lh "$BACKUP"

echo ""
echo "== 2/6 (re)create $SMOKE_DB =="
docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres <<SQL
DROP DATABASE IF EXISTS $SMOKE_DB;
CREATE DATABASE $SMOKE_DB;
SQL

echo ""
echo "== 3/6 import dump → $SMOKE_DB =="
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$SMOKE_DB" < "$BACKUP" > /dev/null

PRE=$(docker compose exec -T db psql -U "$POSTGRES_USER" -d "$SMOKE_DB" -tA <<'SQL'
SELECT
  (SELECT count(*) FROM users)        || ' users / ' ||
  (SELECT count(*) FROM accounts)     || ' accounts / ' ||
  (SELECT count(*) FROM transactions) || ' tx / ' ||
  (SELECT count(*) FROM categories)   || ' cats / ' ||
  (SELECT count(*) FROM budgets)      || ' budgets / ' ||
  (SELECT count(*) FROM goals)        || ' goals / ' ||
  (SELECT count(*) FROM receipts)     || ' receipts';
SQL
)
echo "PRE:  $PRE"

echo ""
echo "== 4/6 docker compose build backend (свежий образ) =="
docker compose build backend

echo ""
echo "== 5/6 alembic upgrade head на $SMOKE_DB =="
# Подменяем DATABASE_URL: тот же db-хост (внутри compose-сети), другая БД.
# `run --rm` НЕ трогает запущенный backend контейнер.
SMOKE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${SMOKE_DB}"
docker compose run --rm -e DATABASE_URL="$SMOKE_URL" backend \
    alembic upgrade head

echo ""
echo "== 6/6 backfill assertions =="
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$SMOKE_DB" \
    -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE
  tbl TEXT;
  n INT;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['accounts','transactions','budgets','goals','receipts']
  LOOP
    EXECUTE format('SELECT count(*) FROM %I WHERE workspace_id IS NULL', tbl) INTO n;
    IF n <> 0 THEN
      RAISE EXCEPTION '% has % rows with NULL workspace_id', tbl, n;
    END IF;
  END LOOP;
END $$;

DO $$
DECLARE n INT;
BEGIN
  SELECT count(*) INTO n FROM categories WHERE workspace_id IS NULL;
  IF n <> 18 THEN
    RAISE EXCEPTION 'system categories: expected 18, got %', n;
  END IF;
END $$;

DO $$
DECLARE n INT;
BEGIN
  SELECT count(*) INTO n FROM users WHERE active_workspace_id IS NULL;
  IF n <> 0 THEN
    RAISE EXCEPTION '% users with NULL active_workspace_id', n;
  END IF;
END $$;

DO $$
DECLARE n INT;
BEGIN
  SELECT count(*) INTO n
  FROM (
    SELECT u.id, count(wm.id) AS c
    FROM users u
    LEFT JOIN workspace_members wm ON wm.user_id = u.id
    GROUP BY u.id
    HAVING count(wm.id) <> 1
  ) s;
  IF n <> 0 THEN
    RAISE EXCEPTION '% users with non-1 memberships (expected 1 personal each)', n;
  END IF;
END $$;
SQL

POST=$(docker compose exec -T db psql -U "$POSTGRES_USER" -d "$SMOKE_DB" -tA <<'SQL'
SELECT
  (SELECT count(*) FROM users)        || ' users / ' ||
  (SELECT count(*) FROM accounts)     || ' accounts / ' ||
  (SELECT count(*) FROM transactions) || ' tx / ' ||
  (SELECT count(*) FROM categories)   || ' cats / ' ||
  (SELECT count(*) FROM budgets)      || ' budgets / ' ||
  (SELECT count(*) FROM goals)        || ' goals / ' ||
  (SELECT count(*) FROM receipts)     || ' receipts';
SQL
)
echo "POST: $POST"
echo "(сравните 7 чисел глазами: миграция не должна терять/множить данные)"

echo ""
echo "== cleanup: drop $SMOKE_DB =="
docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres \
    -c "DROP DATABASE $SMOKE_DB"

echo ""
echo "OK: migration smoke passed."
echo "Backup at $BACKUP — оставлен для rollback до следующего успешного деплоя."
REMOTE
