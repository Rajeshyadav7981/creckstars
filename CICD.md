# CrecKStars — CI/CD, Backups & Play Store Runbook

Companion to `DEPLOYMENT.md` (which covers the manual/first-time setup). This file
covers the **automated** pipeline, **data safety** (backup/restore/export), and the
**Play Store** release path.

```
 push to main (backend repo)
        │
        ▼
 ┌──────────────┐   pass    ┌───────────────┐   ssh    ┌──────────────────────────┐
 │ CI (ci.yml)  │──────────▶│ Deploy        │─────────▶│ VM: scripts/deploy.sh    │
 │ pytest+pg+rd │           │ (deploy.yml)  │          │ backup→pull→migrate→     │
 └──────────────┘           └───────────────┘          │ restart→health→rollback  │
                                                        └──────────────────────────┘
 nightly 03:00 ── systemd timer ── scripts/backup.sh ── DB + uploads ─▶ local + GCS

 tag v1.0.2 (frontend repo) ─▶ release.yml ─▶ EAS build AAB ─▶ eas submit ─▶ Play Console
```

---

## 0. Repository layout (production-ready)

Root holds only what tooling references; everything else is grouped.

```
backend/
├── src/                      # application code (entrypoint: src.app.api.fastapi_app:app)
├── alembic/  alembic.ini     # DB migrations (source of truth for schema deltas)
├── schema.sql                # baseline snapshot, used only for fresh rebuilds
├── requirements.txt  pytest.ini  .python-version
├── .env                      # secrets (gitignored)  ──  .env.example (committed template)
├── releases/                 # APK + version.json (served by the API; .apk via Git LFS)
├── tests/                    # pytest unit/integration tests (CI runs these)
├── tools/                    # MANUAL probes/regressions/seeders (live-server, not CI)
├── scripts/                  # ops: deploy, migrate, backup, restore, export, run_task, systemd units
└── docs guides               # DEPLOYMENT.md (manual/runbook), CICD.md (this file)
```

What moved & why: the 21 loose `probe_*.py`, `*_regression.py`, `seed_*.py` files left
the repo root for `tools/` — they cross-import each other and `src.*` and run against a
**live** backend, so they're operator tools, not tests. Run them via the isolated runner
(below) so they never touch the uvicorn process. CI is unaffected (`pytest.ini` only
collects `tests/test_*.py`).

### Adopt the new systemd unit (one-time, enables migrate-before-startup)
```bash
sudo cp ~/creckstars/scripts/creckstars.service /etc/systemd/system/creckstars.service
sudo systemctl daemon-reload
sudo systemctl restart creckstars
```

---

## 0b. Migrations & zero-downtime

Three layers, all using the **same** idempotent `scripts/migrate.sh`:

1. **Deploy time** — `deploy.sh` runs `migrate.sh` *before* the restart, while the old
   process is still serving. For additive/backward-compatible migrations this is
   effectively zero downtime: new schema is in place, then the new code starts against it.
2. **Startup gate** — the systemd unit's `ExecStartPre=migrate.sh` runs migrations
   before uvicorn boots on every start (incl. VM reboot/crash). If a migration fails,
   the service refuses to start — never serves a half-migrated schema. Runs once, not
   per worker (no migration races).
3. **CI** — `ci.yml` runs `alembic upgrade head` against a throwaway Postgres so a
   broken migration fails the build before it can reach the VM.

```bash
./scripts/migrate.sh            # upgrade to head (idempotent)
./scripts/migrate.sh --check    # exit non-zero if migrations are pending (no change)
./scripts/migrate.sh --sql      # print the SQL it would run (dry run)
```

**Zero-downtime rule (expand → migrate → contract):** never make a single migration
both add and remove. To rename/drop a column: (1) ship a migration that *adds* the new
column + code that writes both; (2) backfill via `run_task.sh`; (3) a later release flips
reads to the new column; (4) a final migration *drops* the old one. Each step is
backward-compatible with the currently-running code, so a restart never breaks.

### Running one-off scripts without affecting the running backend
`scripts/run_task.sh` runs any Python task in an **isolated process** (own venv, `.env`
loaded, `PYTHONPATH` set) — it only opens its own DB/Redis/HTTP connections, so the live
API keeps serving.

```bash
./scripts/run_task.sh tools/seed_demo_tournament.py
./scripts/run_task.sh tools/tournament_regression.py
./scripts/run_task.sh tools/backfill_whatever.py     # ad-hoc maintenance
```
For heavy backfills: run off-peak, keep each statement transactional, and avoid
long-held locks on live tables.

---

## 1. Backend CI/CD

### What runs
| File | Trigger | Does |
|---|---|---|
| `.github/workflows/ci.yml` | push/PR to `main` | spins up Postgres 16 + Redis 7, installs deps, loads `schema.sql`, `alembic upgrade head`, runs `pytest` |
| `.github/workflows/deploy.yml` | CI success on `main` (or manual) | SSHes to the VM and runs `scripts/deploy.sh`, then checks the public `/health` |
| `scripts/deploy.sh` | on the VM | **backup → pull → deps → migrate → restart → health-gate → auto-rollback** |

### One-time setup
1. **SSH key for the runner** (on your machine):
   ```bash
   ssh-keygen -t ed25519 -f deploy_key -C "github-actions" -N ""
   # add the PUBLIC key to the VM:
   ssh-copy-id -i deploy_key.pub ry128037@crixone.in
   ```
2. **GitHub repo secrets** (Settings → Secrets and variables → Actions):
   | Secret | Value |
   |---|---|
   | `VM_HOST` | `crixone.in` |
   | `VM_USER` | `ry128037` |
   | `VM_SSH_KEY` | contents of `deploy_key` (private) |
   | `VM_SSH_PORT` | `22` (optional) |
3. **Passwordless `sudo` for the two service commands** the deploy needs, so SSH
   deploys don't hang on a password prompt. On the VM, `sudo visudo -f /etc/sudoers.d/creckstars`:
   ```
   ry128037 ALL=(ALL) NOPASSWD: /bin/systemctl restart creckstars, /bin/systemctl stop creckstars, /bin/systemctl start creckstars
   ```
4. Ensure `scripts/` is executable on the VM: `chmod +x ~/creckstars/scripts/*.sh`.

### Day-to-day
Just `git push origin main`. CI gates, then it auto-deploys. To deploy by hand:
GitHub → Actions → **Deploy** → *Run workflow*. Or on the VM directly:
`bash ~/creckstars/scripts/deploy.sh`.

### Rollback
- **Code**: automatic — if `/health` fails after restart, `deploy.sh` resets to the
  previous commit and restarts. Manual: `git reset --hard <good-sha> && sudo systemctl restart creckstars`.
- **Bad migration**: code rollback alone won't undo schema changes. Restore the
  `pre-deploy` snapshot taken at the start of the same deploy:
  `./scripts/restore.sh <ts>-pre-deploy`.

---

## 2. Backups (data safety)

> RPO target: ≤24h (nightly) + a fresh snapshot before every deploy. RTO: minutes.

### Scheduled
`scripts/backup.sh` dumps the DB (`pg_dump -Fc`) + `uploads/`, writes a manifest and
SHA256 checksums, copies offsite to GCS, and prunes local copies older than
`RETENTION_DAYS` (default 14).

Install the nightly timer on the VM:
```bash
sudo cp ~/creckstars/scripts/creckstars-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now creckstars-backup.timer
systemctl list-timers creckstars-backup        # confirm next run
```

### Offsite bucket (GCS — you're already on GCP)
```bash
gcloud storage buckets create gs://creckstars-backups --location=<region> --uniform-bucket-level-access
# let the VM's service account write to it:
gcloud storage buckets add-iam-policy-binding gs://creckstars-backups \
  --member="serviceAccount:$(gcloud compute instances describe instance-20260403-052401 \
    --zone=<zone> --format='value(serviceAccounts[0].email)')" \
  --role=roles/storage.objectAdmin
# auto-expire offsite copies after 60 days:
printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":60}}]}' > /tmp/lc.json
gcloud storage buckets update gs://creckstars-backups --lifecycle-file=/tmp/lc.json
```

### Restore
```bash
./scripts/restore.sh 20260623-031500-nightly                       # local
./scripts/restore.sh gs://creckstars-backups/20260623-031500-nightly  # offsite
```
Drops & recreates the DB, reinstalls the 3 superuser extensions, `pg_restore`s data,
restores uploads, restarts, health-checks. **Verifies checksums first.**

### Test your restore (do this monthly — an untested backup is not a backup)
Restore the latest dump into a throwaway DB and count rows:
```bash
sudo -u postgres createdb creckstars_verify
PGPASSWORD=creckstars pg_restore -h localhost -U creckstars -d creckstars_verify \
  --no-owner ~/backups/<latest>/db.dump
psql -h localhost -U creckstars -d creckstars_verify -c "\dt" | tail
sudo -u postgres dropdb creckstars_verify
```

---

## 3. On-demand data export

For migrations, audits, or "export all the data" requests:
```bash
./scripts/export_data.sh        # -> ~/exports/creckstars-export-<ts>.tar.gz
```
Bundle contains: `db.dump` (full restore), `csv/<table>.csv` (every table, spreadsheet-
readable), `schema.sql`, `uploads.tar.gz`, and `SHA256SUMS`.

User-level GDPR-style export/delete already exists in-app (`DELETE /api/auth/me`,
per `PLAY_DATA_SAFETY.md`) — this script is the *operator-level* full export.

---

## 4. Good-practice checklist (hardening)

- [ ] **Secrets**: `.env` is gitignored ✔. Rotate `SECRET_KEY` & `VERIFYNOW_*` off the
      values committed in `DEPLOYMENT.md` (they're now public in that doc).
- [ ] **TLS**: confirm HTTPS terminates properly. The `iptables 80→7981` redirect serves
      HTTP only; put **Caddy or nginx** in front for real certs (auto-renew) instead of
      relying on the redirect, or run Let's Encrypt via certbot.
- [ ] **Firewall**: GCP firewall should expose only 80/443 (+22 from your IP). Postgres
      (5432) and Redis (6379) must **not** be world-reachable — keep them on localhost.
- [ ] **DB least privilege**: app role `creckstars` already lacks createdb/superuser ✔.
- [ ] **Migrations**: make `2026_05_29_stage_label.py` idempotent (`ADD COLUMN IF NOT
      EXISTS`) so fresh builds don't need the manual stamp (see `DEPLOYMENT.md`).
- [ ] **Monitoring**: add an uptime check (GCP Uptime Check or healthchecks.io ping from
      the backup timer) on `/health`; alert on failure.
- [ ] **Backups verified**: run the §2 restore test monthly.
- [ ] **Log rotation**: journald is fine; if you ever switch to nohup/file logs, add logrotate.
- [ ] **CI required**: in GitHub branch protection, mark the `CI` check required to merge.

---

## 5. Frontend → Google Play Store

Today the app self-distributes an APK via `/download/latest`. To ship on Play you need
the **AAB** path (already configured: `eas.json` `production` profile → `app-bundle`,
`autoIncrement: true`).

### Already done ✔
- `app.json`: package `com.creckstars.app`, `versionCode` 2, adaptive icons, deep links.
- `PLAY_DATA_SAFETY.md`: Data Safety form answers prepared from real code.
- `eas.json` production profile builds an AAB; `submit.production` wired to a service account.

### One-time Play Console setup
1. **Create a Google Play Developer account** ($25 one-time) → create app
   "CrecKStars", package `com.creckstars.app`.
2. **App signing**: let Google Play manage the app signing key (Play App Signing —
   default). EAS holds the upload key. First `eas build` will create/manage the
   upload keystore for you (`eas credentials` to inspect).
3. **Service account for automated submit**:
   - Google Cloud Console → IAM → Service Accounts → create → JSON key.
   - Play Console → Setup → API access → link the service account → grant
     "Release to testing tracks / production".
   - Save the JSON as `frontend/play-service-account.json` (gitignored ✔) for local
     `eas submit`, and as the `GOOGLE_SERVICE_ACCOUNT_JSON`/`EXPO_TOKEN` secrets for CI.
4. **Store listing** (in Play Console): app name, short & full description, app icon
   (512×512), feature graphic (1024×500), ≥2 phone screenshots, category (Sports),
   contact email.
5. **Privacy Policy URL** — REQUIRED. Host real Privacy + Terms pages and put the URL in
   the listing AND in `src/screens/profile/SettingsScreen.js` (`PRIVACY_POLICY_URL`,
   `TERMS_URL`) — see the checklist in `PLAY_DATA_SAFETY.md`.
6. **Content rating** questionnaire, **Data safety** form (use `PLAY_DATA_SAFETY.md`),
   **Target audience**, **Ads** (declare none), **Government apps** (no).

### Release flow
**Automated** (after secrets are set):
```bash
cd frontend
# bump version + versionCode in app.json, commit, then:
git tag v1.0.2 && git push origin v1.0.2     # triggers .github/workflows/release.yml
```
That builds the AAB on EAS and submits to the **internal** track.

**Manual**:
```bash
cd frontend
eas build --platform android --profile production   # produces .aab
eas submit --platform android --profile production --latest
```

### Track promotion
Submit to **internal testing** first → verify on a real device → promote to
**closed (alpha/beta)** → **production** in Play Console. First production review can
take a few days; Google requires **20 testers for 14 days** on a closed track before a
personal developer account can publish to production.

### Versioning — keep three things in lockstep on every release
| Where | Field | Notes |
|---|---|---|
| `frontend/app.json` | `version` (e.g. `1.0.2`) + `android.versionCode` (integer, must increase) | `autoIncrement` bumps versionCode on EAS; still bump `version` by hand |
| `backend/releases/version.json` | `latest_version` / `min_version` | drives the in-app update prompt (`DEPLOYMENT.md` §Version Management) |
| Play Console | release track | the store-distributed build |

> Note: the in-app `/download/latest` self-update flow and the Play Store are two
> separate distribution channels. Once you're on Play, decide whether to keep the
> sideload channel (useful for instant hotfixes) or retire it to avoid version drift.
