#!/usr/bin/env bash
# CrecKStars — full backup: PostgreSQL dump + uploads, local + offsite (GCS).
# Run on the VM (cron/systemd timer nightly, and automatically before each deploy).
#
#   ./scripts/backup.sh                 # normal nightly/pre-deploy backup
#   BACKUP_LABEL=pre-deploy ./scripts/backup.sh
#
# Restores: ./scripts/restore.sh <timestamp>
set -euo pipefail

# ---- config (override via env) ------------------------------------------------
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$PROJECT_ROOT")/backups}"
DB_NAME="${DB_NAME:-creckstars}"
DB_USER="${DB_USER:-creckstars}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_PASSWORD="${DB_PASSWORD:-creckstars}"
GCS_BUCKET="${GCS_BUCKET:-gs://creckstars-backups}"   # set to "" to disable offsite
RETENTION_DAYS="${RETENTION_DAYS:-14}"                  # local retention
LABEL="${BACKUP_LABEL:-nightly}"
# ------------------------------------------------------------------------------

TS="$(date +%Y%m%d-%H%M%S)"
STAMP="${TS}-${LABEL}"
DEST="${BACKUP_DIR}/${STAMP}"
mkdir -p "$DEST"
export PGPASSWORD="$DB_PASSWORD"

log() { echo "[backup $(date +%H:%M:%S)] $*"; }

# 1. Database — custom format (-Fc) so restore can be parallel & selective.
log "dumping database '$DB_NAME' ..."
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  --format=custom --no-owner --no-privileges \
  --file="${DEST}/db.dump"

# 2. Uploads (profiles/posts) — user data not in git.
if [ -d "${PROJECT_ROOT}/uploads" ]; then
  log "archiving uploads ..."
  tar -czf "${DEST}/uploads.tar.gz" -C "$PROJECT_ROOT" uploads
fi

# 3. Manifest — what produced this backup (for auditable restores).
cat > "${DEST}/manifest.txt" <<EOF
timestamp:   ${TS}
label:       ${LABEL}
db_name:     ${DB_NAME}
git_commit:  $(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo n/a)
alembic:     $("${PROJECT_ROOT}/venv/bin/alembic" -c "${PROJECT_ROOT}/alembic.ini" current 2>/dev/null | tail -1 || echo n/a)
db_bytes:    $(stat -c%s "${DEST}/db.dump" 2>/dev/null || echo 0)
host:        $(hostname)
EOF

# 4. Checksums — detect silent corruption before you ever need a restore.
( cd "$DEST" && sha256sum ./* > SHA256SUMS )

# 5. Offsite copy to GCS (VM's service account needs objectAdmin on the bucket).
if [ -n "$GCS_BUCKET" ] && command -v gcloud >/dev/null 2>&1; then
  log "uploading to ${GCS_BUCKET}/${STAMP}/ ..."
  # Non-fatal: a missing/unconfigured bucket must not abort a deploy (the local
  # snapshot is still the rollback net). Configure GCS later per CICD.md.
  gcloud storage cp -r "$DEST" "${GCS_BUCKET}/${STAMP}/" --quiet || log "GCS upload failed (non-fatal) — local backup kept"
else
  log "GCS upload skipped (bucket unset or gcloud missing)"
fi

# 6. Local retention prune (offsite uses bucket lifecycle policy — see CICD.md).
find "$BACKUP_DIR" -maxdepth 1 -type d -name '20*' -mtime "+${RETENTION_DAYS}" \
  -exec rm -rf {} + 2>/dev/null || true

log "done -> ${DEST}"
