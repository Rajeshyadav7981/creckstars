#!/usr/bin/env bash
# CrecKStars — backend deploy, run ON THE VM (by hand or by GitHub Actions over SSH).
# Safe by construction: backup -> pull -> deps -> migrate -> restart -> health-gate
# -> auto-rollback to the previous commit if /health fails.
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/home/ry128037/creckstars}"
BRANCH="${DEPLOY_BRANCH:-main}"
HEALTH_URL="${HEALTH_URL:-http://localhost:7981/health}"
VENV="${PROJECT_ROOT}/venv"
cd "$PROJECT_ROOT"

log() { echo "== [deploy $(date +%H:%M:%S)] $*"; }
PREV_COMMIT="$(git rev-parse HEAD)"

# 1. Always snapshot before changing anything — this is the rollback safety net.
log "pre-deploy backup ..."
BACKUP_LABEL=pre-deploy "${PROJECT_ROOT}/scripts/backup.sh" || { echo "backup FAILED — aborting"; exit 1; }

# 2. Fetch new code (+ APK via LFS if present).
log "pulling ${BRANCH} ..."
git fetch --all --quiet
git reset --hard "origin/${BRANCH}"
git lfs pull 2>/dev/null || true
NEW_COMMIT="$(git rev-parse --short HEAD)"

# 3. .env sanity — leading whitespace silently drops prod into dev mode.
if grep -qnE "^[[:space:]]" "${PROJECT_ROOT}/.env" 2>/dev/null; then
  log "stripping leading whitespace from .env"
  sed -i 's/^[[:space:]]*//' "${PROJECT_ROOT}/.env"
fi

# 4. Deps (cheap if unchanged).
log "installing deps ..."
"${VENV}/bin/pip" install -q -r requirements.txt

# 5. Migrations BEFORE restart, while the old process keeps serving (additive/
#    backward-compatible migrations = effectively zero downtime). Single outer
#    transaction; failure rolls itself back. systemd ExecStartPre re-runs this
#    (idempotent) so a bare reboot also migrates before booting.
log "migrating schema ..."
"${PROJECT_ROOT}/scripts/migrate.sh"

# 6. Restart + health gate.
log "restarting service ..."
sudo systemctl restart creckstars
for i in $(seq 1 15); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    log "healthy at ${NEW_COMMIT} ✔"
    exit 0
  fi
  sleep 2
done

# 7. Health failed -> roll the code back and restart. (DB: restore manually from the
#    pre-deploy backup only if a migration is the culprit — see CICD.md.)
log "HEALTH CHECK FAILED — rolling back to ${PREV_COMMIT}"
git reset --hard "$PREV_COMMIT"
"${VENV}/bin/pip" install -q -r requirements.txt
sudo systemctl restart creckstars
sleep 3
curl -fsS "$HEALTH_URL" >/dev/null 2>&1 && log "rolled back, healthy again" || log "STILL UNHEALTHY — manual intervention needed"
exit 1
