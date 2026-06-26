#!/usr/bin/env bash
# CrecKStars — restore DB + uploads from a backup created by backup.sh.
#
#   ./scripts/restore.sh 20260623-031500-nightly         # from local backup dir
#   ./scripts/restore.sh gs://creckstars-backups/20260623-031500-nightly   # from GCS
#
# DESTRUCTIVE: drops & recreates the database. Stops the API first, restarts after.
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/home/ry128037/creckstars}"
BACKUP_DIR="${BACKUP_DIR:-/home/ry128037/backups}"
DB_NAME="${DB_NAME:-creckstars}"
DB_USER="${DB_USER:-creckstars}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_PASSWORD="${DB_PASSWORD:-creckstars}"
export PGPASSWORD="$DB_PASSWORD"

SRC="${1:?usage: restore.sh <backup-timestamp | gs://.../timestamp>}"
log() { echo "[restore] $*"; }

# Pull from GCS into a temp dir if a bucket path was given.
if [[ "$SRC" == gs://* ]]; then
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  log "fetching $SRC ..."
  gcloud storage cp -r "${SRC%/}/*" "$TMP/" --quiet
  DIR="$TMP"
else
  DIR="${SRC}"; [[ "$DIR" = /* ]] || DIR="${BACKUP_DIR}/${SRC}"
fi
[ -f "${DIR}/db.dump" ] || { echo "no db.dump in ${DIR}"; exit 1; }

# Verify integrity before touching anything.
if [ -f "${DIR}/SHA256SUMS" ]; then
  log "verifying checksums ..."
  ( cd "$DIR" && sha256sum -c SHA256SUMS )
fi

read -r -p "This DROPS database '${DB_NAME}'. Type the db name to confirm: " ok
[ "$ok" = "$DB_NAME" ] || { echo "aborted"; exit 1; }

log "stopping API ..."
sudo systemctl stop creckstars

log "recreating database ..."
sudo -u postgres psql -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DB_NAME}' AND pid<>pg_backend_pid();"
sudo -u postgres psql -d postgres -c "DROP DATABASE IF EXISTS ${DB_NAME};"
sudo -u postgres psql -d postgres -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
# Extensions are superuser-only and live outside the dump.
for ext in pg_trgm cube earthdistance; do
  sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS ${ext};"
done

log "restoring data (pg_restore) ..."
pg_restore -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  --no-owner --no-privileges --jobs=4 "${DIR}/db.dump" || true  # non-fatal: ext/owner notices

log "restoring uploads ..."
if [ -f "${DIR}/uploads.tar.gz" ]; then
  rm -rf "${PROJECT_ROOT}/uploads"
  tar -xzf "${DIR}/uploads.tar.gz" -C "$PROJECT_ROOT"
fi

log "starting API ..."
sudo systemctl start creckstars
sleep 2
curl -fsS http://localhost:7981/health && echo " <- healthy"
log "restore complete from ${DIR}"
