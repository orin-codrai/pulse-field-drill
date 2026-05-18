#!/usr/bin/env bash
# check-env.sh — сверяет имена переменных в .env vs .env.example.
#
# Usage: check-env.sh [EXAMPLE_FILE] [ENV_FILE]
#   defaults: ./.env.example ./.env
#
# Exit codes:
#   0 — все required переменные присутствуют (могут быть пусты — это ок).
#   1 — хотя бы одна required переменная отсутствует в .env. Сообщение
#       печатается с готовыми строками для копи-пасты.
#
# Stale-переменные (есть в .env, нет в example) выводятся как WARN,
# не блокирующая ошибка — могут быть legacy/local-override.

set -euo pipefail

EXAMPLE_FILE="${1:-./.env.example}"
ENV_FILE="${2:-./.env}"

if [[ ! -f "$EXAMPLE_FILE" ]]; then
    echo "FATAL: example file not found: $EXAMPLE_FILE" >&2
    exit 2
fi

# .env может ещё не существовать на свежей машине — обработаем как пустой.
ENV_CONTENT=""
if [[ -f "$ENV_FILE" ]]; then
    ENV_CONTENT="$(cat "$ENV_FILE")"
fi

# Имена переменных: ^NAME= в начале строки (без комментариев, без пустых).
extract_names() {
    grep -E '^[A-Z_][A-Z0-9_]*=' "$@" 2>/dev/null | cut -d= -f1 | sort -u
}

example_names="$(extract_names "$EXAMPLE_FILE")"
env_names="$(echo "$ENV_CONTENT" | grep -E '^[A-Z_][A-Z0-9_]*=' | cut -d= -f1 | sort -u || true)"

# Missing: в example, но не в .env.
missing="$(comm -23 <(echo "$example_names") <(echo "$env_names") || true)"
# Stale: в .env, но не в example.
stale="$(comm -13 <(echo "$example_names") <(echo "$env_names") || true)"

if [[ -n "$missing" ]]; then
    n=$(echo "$missing" | wc -l)
    echo "MISSING in $ENV_FILE ($n):" >&2
    echo "$missing" | sed 's/^/  /' >&2
    echo "" >&2
    echo "Add these lines (copy from $EXAMPLE_FILE, fill in secrets):" >&2
    for var in $missing; do
        # Достаём ВСЮ строку из example (с дефолтным значением, если есть).
        grep -E "^${var}=" "$EXAMPLE_FILE" | head -1 | sed 's/^/  /' >&2
    done
    echo "" >&2
    echo "Fix $ENV_FILE and rerun." >&2
    exit 1
fi

if [[ -n "$stale" ]]; then
    n=$(echo "$stale" | wc -l)
    echo "WARN: $ENV_FILE has $n variable(s) not in $EXAMPLE_FILE (legacy / local override?):" >&2
    echo "$stale" | sed 's/^/  /' >&2
fi

echo "OK: all variables from $EXAMPLE_FILE present in $ENV_FILE."
