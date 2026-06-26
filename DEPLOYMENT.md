# CrecKStars — Deployment & Release Guide

Complete guide for building the APK, deploying the backend, releasing updates, and managing app versions.

---

## Table of Contents

0. [Deploy New Changes (Quick Steps)](#deploy-new-changes-quick-steps)
1. [VM — At-a-Glance Reference](#vm--at-a-glance-reference)
2. [Architecture Overview](#architecture-overview)
3. [Backend Deployment](#backend-deployment)
4. [Fresh DB Wipe & Rebuild](#fresh-db-wipe--rebuild)
5. [Common Pitfalls](#common-pitfalls)
6. [Build APK](#build-apk)
7. [Upload APK & Release](#upload-apk--release)
8. [Version Management](#version-management)
9. [Auto-Update Flow](#auto-update-flow)
10. [Landing Page](#landing-page)
11. [Quick Reference Commands](#quick-reference-commands)

---

## Deploy New Changes (Quick Steps)

> The 90% case. For the automated pipeline (push → CI → auto-deploy) and rollback, see `CICD.md`.

**Scripted (recommended)** — handles order, backup, migrate, health-check, auto-rollback:

```bash
# Local: push
cd backend && git add . && git commit -m "changes" && git push origin main
# VM: one command
cd ~/creckstars && bash scripts/deploy.sh
```

**Manual** — always in this order: **pull → deps → migrate → restart**:

```bash
cd ~/creckstars
git pull origin main
~/creckstars/venv/bin/pip install -r requirements.txt   # only if requirements.txt changed
~/creckstars/venv/bin/alembic upgrade head              # only if a new migration was added
git lfs pull                                            # only if a new APK was pushed
sudo systemctl restart creckstars                       # restart LAST
curl -sS http://localhost:7981/health                   # verify
```

If startup logs say "development-mode settings," `.env` has leading whitespace:
`sed -i 's/^[[:space:]]*//' ~/creckstars/.env && sudo systemctl restart creckstars`.

---

## VM — At-a-Glance Reference

> Single source of truth for "what's running where, with what creds." Update this whenever any of these change.

### Host

| Item | Value |
|------|-------|
| Public hostname | `crixone.in` (always use this — never the raw IP) |
| GCP instance name | `instance-20260403-052401` |
| Shell user | `ry128037` |
| Project root on VM | `/home/ry128037/creckstars` |
| SSH | `gcloud compute ssh instance-20260403-052401 --zone=<your-zone>` |

### Backend service (FastAPI)

| Item | Value |
|------|-------|
| systemd unit | `creckstars.service` |
| Internal port | `7981` |
| External | `:80 → :7981` via `iptables -t nat PREROUTING`, HTTPS via duckdns / reverse proxy |
| Python | `/home/ry128037/creckstars/venv/bin/python3.11` |
| Entrypoint | `uvicorn src.app.api.fastapi_app:app --host 0.0.0.0 --port 7981` |
| Env file | `/home/ry128037/creckstars/.env` |
| Uploads dir | `/home/ry128037/creckstars/uploads/{profiles,posts}` (on-disk, B2/S3 reverted) |
| Health | `https://crixone.in/health` → `{"status":"ok",...}` |

### PostgreSQL (local on the VM, not Supabase)

| Item | Value |
|------|-------|
| Host / port | `localhost:5432` |
| Database | `creckstars` |
| App role | user `creckstars` / password `creckstars` (owner of all public tables) |
| Superuser | `postgres` (used via `sudo -u postgres psql` for `CREATE DATABASE` / `CREATE EXTENSION` / schema.sql load) |
| Required extensions | `pg_trgm`, `cube`, `earthdistance` (all 3 are superuser-only to install) |
| Schema file | `~/creckstars/schema.sql` (27 tables, 60+ indexes — current baseline snapshot) |
| Alembic head | `b7d3f9a1c204` (run `alembic current` to confirm) |

Quick connect (set `PGPASSWORD` once per shell):

```bash
export PGPASSWORD=creckstars
psql -h localhost -U creckstars -d creckstars
```

### Redis

| Item | Value |
|------|-------|
| Host / port | `localhost:6379` (default, no auth) |
| Used for | WebSocket pub/sub fan-out, VerifyNow OTP `vid` tracking, idempotency keys |
| OTP key shape | `verifynow:vid:{mobile}:{purpose}` (purpose ∈ `register`, `login`, `reset_password`) |
| Flush all | `redis-cli FLUSHALL` (does **not** restart the service — safe) |

### OTP (Message Central VerifyNow)

- No DLT; SMS OTP goes through the VerifyNow managed verify API (Fast2SMS removed).
- VerifyNow tracks one active `verificationId` per mobile (purpose-agnostic). The backend aliases the active VID under each purpose key in Redis so a `register` OTP can be reused for `login`/`reset_password` within its TTL (5 min) — prevents 506 lockouts when users switch flows.

### .env shape (sensitive — do not commit)

```dotenv
ENVIRONMENT=production
SECRET_KEY=creckstars-production-secret-key-2024-super-strong

DB_HOST=localhost
DB_PORT=5432
DB_USER=creckstars
DB_PASSWORD=creckstars
DB_NAME=creckstars

REDIS_URL=redis://localhost:6379/0

VERIFYNOW_CUSTOMER_ID=...
VERIFYNOW_AUTH_TOKEN=...
# (other secrets as needed)
```

> ⚠️ **No leading whitespace on any line** — pydantic-settings + `source <(grep ...)` both break silently if any line starts with a space. Sanity check: `grep -nE "^[[:space:]]" ~/creckstars/.env` must return zero lines.

### Service management cheatsheet

```bash
# Status / restart / stop / start
sudo systemctl status   creckstars --no-pager | head -25
sudo systemctl restart  creckstars
sudo systemctl stop     creckstars
sudo systemctl start    creckstars

# Logs
sudo journalctl -u creckstars -n 50 --no-pager      # last 50 lines
sudo journalctl -u creckstars -f                    # follow live
sudo journalctl -u creckstars --since "10 min ago"

# Quick smoke
curl -sS https://crixone.in/health
```

---

---

## Architecture Overview

```
┌──────────────────┐     ┌──────────────────────────────────────┐
│   React Native   │     │   Google Cloud VM                     │
│   Frontend       │────▶│   crixone.in              │
│   (APK on phone) │     │   (instance-20260403-052401)          │
└──────────────────┘     │                                        │
                          │   ├── FastAPI / uvicorn  (:7981)       │
                          │   ├── PostgreSQL local   (:5432)       │
                          │   │     ext: pg_trgm, cube,            │
                          │   │          earthdistance             │
                          │   ├── Redis              (:6379)       │
                          │   ├── Uploads on disk    (uploads/)    │
                          │   ├── /                → Landing       │
                          │   ├── /api/*           → API           │
                          │   ├── /download/latest → APK           │
                          │   └── /api/app/version → Version       │
                          └──────────────────────────────────────┘
```

| URL | Purpose |
|-----|---------|
| `https://crixone.in/` | Landing page (shareable, has download button) |
| `https://crixone.in/api/*` | All backend APIs |
| `https://crixone.in/download/latest` | Direct APK download |
| `https://crixone.in/api/app/version` | Version check API |
| `https://crixone.in/health` | Health check |

---

## Backend Deployment

### First-time setup (already done)

```bash
# SSH into VM
gcloud compute ssh instance-20260403-052401 --zone=<YOUR_ZONE>

# Clone repo
git clone https://github.com/Rajeshyadav7981/creckstars.git
cd creckstars

# Setup Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Setup systemd service
sudo nano /etc/systemd/system/creckstars.service
```

Service file (`/etc/systemd/system/creckstars.service`):
```ini
[Unit]
Description=CrecKStars API
After=network.target

[Service]
Type=simple
User=ry128037
WorkingDirectory=/home/ry128037/creckstars
ExecStart=/home/ry128037/creckstars/venv/bin/uvicorn src.app.api.fastapi_app:app --host 0.0.0.0 --port 7981
Restart=always
RestartSec=5
Environment=PATH=/home/ry128037/creckstars/venv/bin:/usr/bin

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable creckstars
sudo systemctl start creckstars

# Port 80 → 7981 redirect (so no :7981 in URLs)
sudo iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 7981
```

### Deploy backend updates

Every time you push code changes:

```bash
# On your local machine
cd backend
git add .
git commit -m "your commit message"
git push origin main

# On the VM (SSH in)
cd ~/creckstars
git pull origin main
sudo systemctl restart creckstars

# Verify
curl http://localhost:7981/health
```

---

## Fresh DB Wipe & Rebuild

Use this when you want to wipe **all** application data on the VM and restart from a known-empty state. `schema.sql` is the frozen baseline; alembic migrations are deltas on top. Post-baseline `ADD COLUMN` migrations are idempotent (`ADD COLUMN IF NOT EXISTS`), so the rebuild is **stamp-free**: load `schema.sql`, then `alembic upgrade head` — no manual `alembic stamp` gymnastics. (Verified end-to-end on a clean DB.)

```bash
# 0. Stop the API so nothing reconnects mid-wipe
sudo systemctl stop creckstars
export PGPASSWORD=creckstars

# 1. Drop & recreate the database (postgres superuser — creckstars role
#    has connect/owner privileges but NOT createdb)
sudo -u postgres psql -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='creckstars' AND pid<>pg_backend_pid();"
sudo -u postgres psql -d postgres -c "DROP DATABASE IF EXISTS creckstars;"
sudo -u postgres psql -d postgres -c "CREATE DATABASE creckstars OWNER creckstars;"

# 2. Extensions (superuser-only)
sudo -u postgres psql -d creckstars -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
sudo -u postgres psql -d creckstars -c "CREATE EXTENSION IF NOT EXISTS cube;"
sudo -u postgres psql -d creckstars -c "CREATE EXTENSION IF NOT EXISTS earthdistance;"

# 3. Load the baseline schema (27 tables + 60+ indexes)
sudo -u postgres psql -d creckstars -f ~/creckstars/schema.sql

# 4. Re-own everything to the app role (schema.sql created objects as
#    postgres; without this, alembic upgrade fails with
#    "must be owner of table ..." on the first ALTER)
sudo -u postgres psql -d creckstars -c "DO \$\$ DECLARE r RECORD; BEGIN FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP EXECUTE format('ALTER TABLE public.%I OWNER TO creckstars', r.tablename); END LOOP; FOR r IN SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema='public' LOOP EXECUTE format('ALTER SEQUENCE public.%I OWNER TO creckstars', r.sequence_name); END LOOP; END \$\$;"

# 5. Apply migrations (stamp-free). The baseline revision is an empty no-op
#    and every post-baseline ADD COLUMN is idempotent, so a single
#    `upgrade head` over the freshly-loaded schema.sql lands at head cleanly.
cd ~/creckstars
~/creckstars/venv/bin/alembic upgrade head

# 6. Verify head + table count
~/creckstars/venv/bin/alembic current      # expect: b7d3f9a1c204 (head)
psql -h localhost -U creckstars -d creckstars -c "\dt" | tail -5

# 7. Flush Redis + clear uploads
redis-cli FLUSHALL
rm -rf ~/creckstars/uploads/*
mkdir -p ~/creckstars/uploads/{profiles,posts}

# 8. Refresh perf deps (only needed after requirements.txt changes)
~/creckstars/venv/bin/pip install -r requirements.txt
~/creckstars/venv/bin/python -c "import uvloop, httptools, orjson; print(uvloop.__version__, httptools.__version__, orjson.__version__)"

# 9. Start + smoke
sudo systemctl start creckstars
sleep 2
sudo systemctl status creckstars --no-pager | head -15
curl -sS https://crixone.in/health
```

---

## Common Pitfalls

Things that bit us in past deploys — check here first when something looks wrong.

### `.env` leading whitespace silently breaks production mode

If any line in `~/creckstars/.env` starts with a space/tab, `source <(grep ...)` returns empty and pydantic-settings falls back to dev defaults — startup logs warn `Running with development-mode settings`. Fix:

```bash
sed -i 's/^[[:space:]]*//' ~/creckstars/.env
grep -nE "^[[:space:]]" ~/creckstars/.env   # must be empty
sudo systemctl restart creckstars
```

### `creckstars` role can't CREATE DATABASE / CREATE EXTENSION

By design — least-privilege. Use `sudo -u postgres psql -d postgres -c "..."` for those two operations only. All other ops (ALTER, INSERT, SELECT, alembic) run as `creckstars`.

### Alembic uses a single outer transaction

`alembic/env.py` runs all pending migrations in **one** transaction. If migration N fails, migrations 1..N-1 also roll back. After a fix, just re-run `alembic upgrade head` — no manual cleanup needed unless you stamped over a partial state.

### Post-baseline `ADD COLUMN` migrations are idempotent (resolved)

`schema.sql` includes `stage_label`, which used to make a fresh build error with
`column "stage_label" already exists`. That migration (and the other post-baseline
`ADD COLUMN` deltas) now use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so a fresh
build is stamp-free: load `schema.sql`, then `alembic upgrade head`. No `alembic
stamp` workaround is needed.

### `fall_of_wickets` must be deleted before `deliveries`

There's no cascade. When wiping per-match state, delete in this order: `fall_of_wickets → deliveries → overs → partnerships → innings → match_squads → matches`. The reset-match endpoint already handles this; bespoke cleanup scripts must too.

### Earthdistance / nearby queries need cube + earthdistance

If `/api/.../nearby` returns 500 with `function ll_to_earth does not exist`, the extensions were not installed (or were installed as a non-superuser and silently no-op'd). Fix:

```bash
sudo -u postgres psql -d creckstars -c "CREATE EXTENSION IF NOT EXISTS cube;"
sudo -u postgres psql -d creckstars -c "CREATE EXTENSION IF NOT EXISTS earthdistance;"
```

### VerifyNow 506 when switching OTP purposes

VerifyNow only allows one active OTP per mobile (purpose-agnostic). The backend handles this by aliasing the active `verificationId` in Redis under each purpose key (`verifynow:vid:{mobile}:{purpose}`). If 506s come back, flush the affected key:

```bash
redis-cli --scan --pattern "verifynow:vid:9XXXXXXXXX:*"
redis-cli DEL verifynow:vid:9XXXXXXXXX:register   # etc
```

### Stray node processes alongside FastAPI

If the VM ever feels slow, check for orphan node/uvicorn processes from past experiments:

```bash
ps auxf | grep -E "node|uvicorn" | grep -v grep
sudo ss -ltnp | grep -E ":7981|node"
```

The legitimate backend is `creckstars.service` only — anything else is fair game to kill.

---

## Build APK

### Prerequisites

- Node.js 18+
- Expo CLI: `npm install -g expo-cli`
- EAS CLI: `npm install -g eas-cli`
- Expo account: `eas login`

### Build with EAS (recommended)

```bash
cd frontend

# First time: configure EAS
eas build:configure

# Build APK (preview profile)
eas build --platform android --profile preview
```

Wait for the build to complete on Expo servers. Download the APK from the link provided.

### Build locally (alternative)

```bash
cd frontend

# Generate native project
npx expo prebuild --platform android

# Build APK
cd android
./gradlew assembleRelease

# APK is at:
# android/app/build/outputs/apk/release/app-release.apk
```

### After building

Rename the APK with version number:
```bash
mv downloaded-build.apk CreckStars.1.0.1.apk
```

---

## Upload APK & Release

### Step 1: Copy APK to backend releases

```bash
# From your local machine
cp CreckStars.1.0.1.apk /path/to/creckstars/backend/releases/
```

### Step 2: Push APK to GitHub (via Git LFS)

```bash
cd backend

# Git LFS tracks .apk files (already configured)
git add releases/CreckStars.1.0.1.apk
git commit -m "Release v1.0.1"
git push origin main
```

> **Note:** APK files are tracked via Git LFS (Large File Storage).
> First-time setup: `git lfs install && git lfs track "releases/*.apk"`

### Step 3: Pull on VM

```bash
# SSH into VM
cd ~/creckstars

# Install git-lfs if not already
sudo apt-get install git-lfs -y
git lfs install

# Pull code + APK
git pull origin main

# Verify APK is there (should show ~80MB, not 130 bytes)
ls -lh releases/*.apk
```

If APK shows ~130 bytes (LFS pointer not resolved):
```bash
git lfs pull
```

### Step 4: Update version.json

```bash
# On the VM
cat > ~/creckstars/releases/version.json << 'EOF'
{
  "latest_version": "1.0.1",
  "latest_version_code": 2,
  "min_version": "1.0.0",
  "release_notes": "Tournament setup improvements, performance optimizations."
}
EOF
```

### Step 5: Restart backend (optional — version.json is read live)

```bash
sudo systemctl restart creckstars
```

> **Note:** version.json is read on every API call — no restart needed for version changes. Only restart if you pulled new Python code.

### Step 6: Verify

```bash
# Version API
curl http://localhost:7981/api/app/version

# APK download
curl -s -o /dev/null -w "HTTP %{http_code}, Size: %{size_download} bytes\n" http://localhost:7981/download/latest

# Landing page
curl -s http://localhost:7981/ | head -3
```

---

## Version Management

### version.json

Located at `backend/releases/version.json`. Read live by the API — **no server restart needed** to change versions.

```json
{
  "latest_version": "1.0.1",
  "latest_version_code": 2,
  "min_version": "1.0.0",
  "release_notes": "Bug fixes and performance improvements."
}
```

| Field | Purpose |
|-------|---------|
| `latest_version` | Newest available version (shown to users) |
| `latest_version_code` | Integer version code (for programmatic comparison) |
| `min_version` | Minimum version allowed — older versions get **force update** |
| `release_notes` | Shown in the update alert and landing page |

### Version comparison logic

The API at `/api/app/version?current=1.0.0` compares versions:

```
current < min_version     → force_update: true   (blocking alert, can't dismiss)
current < latest_version  → update_available: true (optional alert with "Later")
current >= latest_version → no alert              (app opens normally)
```

### Examples

**Optional update** (users can skip):
```json
{
  "latest_version": "1.1.0",
  "min_version": "1.0.0"
}
```
Users on v1.0.0 see: *"Update Available — CrecKStars v1.1.0 is available"* with **Update** / **Later**.

**Force update** (users must update):
```json
{
  "latest_version": "1.1.0",
  "min_version": "1.1.0"
}
```
Users on v1.0.0 see: *"Update Required"* — **cannot dismiss**, only **Update Now** button.

**No update** (current version):
```json
{
  "latest_version": "1.0.0",
  "min_version": "1.0.0"
}
```
Users on v1.0.0: app opens normally, no alert.

---

## Auto-Update Flow

```
User opens app
    │
    ▼
AppNavigator.js calls useUpdateCheck()
    │
    ▼
GET https://crixone.in/api/app/version?current=1.0.0
    │
    ▼
Server reads releases/version.json, compares versions
    │
    ├── force_update: true
    │       → Blocking alert: "Update Required"
    │       → Only "Update Now" button (can't dismiss)
    │       → Opens /download/latest in browser
    │
    ├── update_available: true
    │       → Alert: "Update Available"
    │       → "Update" and "Later" buttons
    │       → "Update" opens /download/latest
    │       → "Later" dismisses, app continues
    │
    └── No update needed
            → App opens normally
```

---

## Landing Page

The root URL `https://crixone.in/` serves a branded landing page with:

- App logo and name
- Feature list (Live Scoring, Tournaments, Teams, Community, Stats)
- **Download APK** button → links to `/download/latest`
- Current version number and release notes
- OG meta tags for WhatsApp/Telegram link previews

The landing page reads version info from `releases/version.json` live — updates automatically when you change the file.

### Share links

| URL | Shows |
|-----|-------|
| `/share/match/42` | Match info page with "Open in App" / "Download" |
| `/share/tournament/5` | Tournament info with download |
| `/share/team/3` | Team profile with download |
| `/share/player/10` | Player profile with download |

All share pages include OG tags so WhatsApp/Telegram show rich previews.

---

## Quick Reference Commands

### Full release cycle (local machine → cloud)

```bash
# 1. Build APK
cd frontend
eas build --platform android --profile preview
# Download APK, rename to CreckStars.X.Y.Z.apk

# 2. Copy to backend releases
cp CreckStars.X.Y.Z.apk ../backend/releases/

# 3. Push to GitHub
cd ../backend
git add releases/CreckStars.X.Y.Z.apk
git commit -m "Release vX.Y.Z"
git push origin main

# 4. On VM (SSH in)
cd ~/creckstars
git pull origin main
git lfs pull  # if APK shows as 130 bytes

# 5. Update version
cat > releases/version.json << EOF
{
  "latest_version": "X.Y.Z",
  "latest_version_code": N,
  "min_version": "1.0.0",
  "release_notes": "What changed in this release."
}
EOF

# 6. Restart if code changed
sudo systemctl restart creckstars

# 7. Verify
curl http://localhost:7981/api/app/version
```

### Backend-only deploy (no APK change)

```bash
# Local
cd backend
git add . && git commit -m "changes" && git push origin main

# VM
cd ~/creckstars && git pull origin main && sudo systemctl restart creckstars
```

### Check server status

```bash
# On VM
sudo systemctl status creckstars
curl http://localhost:7981/health
curl http://localhost:7981/api/app/version
```

### View server logs

```bash
# On VM
sudo journalctl -u creckstars -f          # live logs
sudo journalctl -u creckstars --since "1 hour ago"  # recent logs
cat ~/creckstars/nohup.out                 # if using nohup
```

### Emergency: restart backend

```bash
sudo systemctl restart creckstars
# or if systemd not working:
fuser -k 7981/tcp
cd ~/creckstars && source venv/bin/activate
nohup uvicorn src.app.api.fastapi_app:app --host 0.0.0.0 --port 7981 &
```

---

## File Structure

```
backend/
├── releases/
│   ├── version.json          ← Version config (read live, no restart)
│   └── CreckStars.1.0.0.apk  ← APK file (served via /download/latest)
├── src/
│   └── app/api/
│       ├── fastapi_app.py    ← Landing page at /
│       ├── config.py         ← APK_DIR, version reader
│       └── routers/
│           └── share_router.py ← /api/app/version, /download/latest, /share/*
├── .gitattributes            ← Git LFS tracking for *.apk
├── DEPLOYMENT.md             ← This file
└── ...
```
