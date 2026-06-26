#!/usr/bin/env bash
# CrecKStars — apply DB migrations. Idempotent: safe to run on every deploy AND
# as systemd ExecStartPre (so the schema is always current before uvicorn boots).
#
#   ./scripts/migrate.sh            # upgrade to head
#   ./scripts/migrate.sh --check    # fail (non-zero) if NOT at head, change nothing
#   ./scripts/migrate.sh --sql      # print the SQL that WOULD run, apply nothing
set -euo pipefail

# Default to the repo root inferred from this script's location, so it works on
# any VM/user without editing. Override with PROJECT_ROOT=... if needed.
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ALEMBIC="${PROJECT_ROOT}/venv/bin/alembic"
cd "$PROJECT_ROOT"

case "${1:-}" in
  --check)
    cur="$("$ALEMBIC" current 2>/dev/null | tail -1)"
    head="$("$ALEMBIC" heads 2>/dev/null | tail -1)"
    echo "current: ${cur:-<none>}"
    echo "head:    ${head}"
    case "$cur" in *"${head%% *}"*) echo "up to date"; exit 0;; esac
    echo "PENDING MIGRATIONS"; exit 1
    ;;
  --sql)
    # Dry run: render the upgrade as SQL without touching the DB.
    "$ALEMBIC" upgrade head --sql
    ;;
  *)
    echo "[migrate] current: $("$ALEMBIC" current 2>/dev/null | tail -1)"
    "$ALEMBIC" upgrade head
    echo "[migrate] now at:  $("$ALEMBIC" current 2>/dev/null | tail -1)"
    ;;
esac
