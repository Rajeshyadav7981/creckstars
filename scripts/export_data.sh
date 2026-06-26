#!/usr/bin/env bash
# CrecKStars — on-demand portable data export (for migration, audit, or "give me
# all the data" requests). Produces ONE self-contained .tar.gz:
#   - db.dump      : pg_dump custom format (full restore)
#   - csv/*.csv    : every public table as CSV (human/spreadsheet readable)
#   - schema.sql   : plain-SQL schema snapshot
#   - uploads.tar.gz
#
#   ./scripts/export_data.sh                  # -> ~/exports/creckstars-export-<ts>.tar.gz
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/home/ry128037/creckstars}"
OUT_DIR="${OUT_DIR:-/home/ry128037/exports}"
DB_NAME="${DB_NAME:-creckstars}"
DB_USER="${DB_USER:-creckstars}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_PASSWORD="${DB_PASSWORD:-creckstars}"
export PGPASSWORD="$DB_PASSWORD"

TS="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
STAGE="${WORK}/creckstars-export-${TS}"
mkdir -p "${STAGE}/csv"
mkdir -p "$OUT_DIR"
psql() { command psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$@"; }

echo "[export] full dump ..."
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  --format=custom --no-owner --no-privileges --file="${STAGE}/db.dump"

echo "[export] plain schema ..."
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  --schema-only --no-owner --no-privileges --file="${STAGE}/schema.sql"

echo "[export] per-table CSV ..."
for t in $(psql -At -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"); do
  psql -c "\copy public.\"${t}\" TO '${STAGE}/csv/${t}.csv' WITH CSV HEADER"
done

if [ -d "${PROJECT_ROOT}/uploads" ]; then
  echo "[export] uploads ..."
  tar -czf "${STAGE}/uploads.tar.gz" -C "$PROJECT_ROOT" uploads
fi

( cd "$STAGE" && sha256sum $(find . -type f | sed 's|^\./||') > SHA256SUMS )

ARCHIVE="${OUT_DIR}/creckstars-export-${TS}.tar.gz"
tar -czf "$ARCHIVE" -C "$WORK" "creckstars-export-${TS}"
echo "[export] done -> ${ARCHIVE}"
echo "         restore the .dump with: pg_restore -d <db> --no-owner db.dump"
