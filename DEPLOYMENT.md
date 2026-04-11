# CrecKStars — Deployment & Release Guide

Complete guide for building the APK, deploying the backend, releasing updates, and managing app versions.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Backend Deployment](#backend-deployment)
3. [Build APK](#build-apk)
4. [Upload APK & Release](#upload-apk--release)
5. [Version Management](#version-management)
6. [Auto-Update Flow](#auto-update-flow)
7. [Landing Page](#landing-page)
8. [Quick Reference Commands](#quick-reference-commands)

---

## Architecture Overview

```
┌──────────────────┐     ┌──────────────────────────────────┐
│   React Native   │     │   Google Cloud VM                 │
│   Frontend       │────▶│   34.14.185.224                   │
│   (APK on phone) │     │   creckstars.duckdns.org          │
└──────────────────┘     │                                    │
                          │   ├── FastAPI backend (:7981)      │
                          │   ├── PostgreSQL (Supabase)        │
                          │   ├── Redis                        │
                          │   ├── /                → Landing   │
                          │   ├── /api/*           → API       │
                          │   ├── /download/latest → APK       │
                          │   └── /api/app/version → Version   │
                          └──────────────────────────────────┘
```

| URL | Purpose |
|-----|---------|
| `http://creckstars.duckdns.org/` | Landing page (shareable, has download button) |
| `http://creckstars.duckdns.org/api/*` | All backend APIs |
| `http://creckstars.duckdns.org/download/latest` | Direct APK download |
| `http://creckstars.duckdns.org/api/app/version` | Version check API |
| `http://creckstars.duckdns.org/health` | Health check |

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
GET http://creckstars.duckdns.org/api/app/version?current=1.0.0
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

The root URL `http://creckstars.duckdns.org/` serves a branded landing page with:

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
