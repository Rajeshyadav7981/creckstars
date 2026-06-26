#!/usr/bin/env bash
# CrecKStars — run a one-off Python task (seed / probe / maintenance) in an
# ISOLATED process that does NOT touch the running uvicorn service.
#
# It only opens its own DB/Redis/HTTP connections, so the live backend keeps
# serving. Use it for seeders, data backfills, and ad-hoc maintenance.
#
#   ./scripts/run_task.sh tools/seed_demo_tournament.py
#   ./scripts/run_task.sh tools/probe_favorites.py
#   API_URL=http://localhost:7981 ./scripts/run_task.sh tools/tournament_regression.py
#
# Notes:
#   - Loads .env so the task sees the same DB creds as the app.
#   - PYTHONPATH=project root so `from src...` and sibling `import tournament_regression`
#     both resolve.
#   - For heavy backfills, run off-peak and keep each statement transactional so
#     you never hold long locks against the live tables.
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/home/ry128037/creckstars}"
PY="${PROJECT_ROOT}/venv/bin/python"
SCRIPT="${1:?usage: run_task.sh <path/to/script.py> [args...]}"
shift || true
cd "$PROJECT_ROOT"

# Export non-comment, non-blank lines from .env into this process only.
if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${PROJECT_ROOT}/.env")
  set +a
fi

echo "[run_task] $(basename "$SCRIPT")  (isolated; live backend untouched)"
PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}" exec "$PY" "$SCRIPT" "$@"
