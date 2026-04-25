"""
Share / Deep Link / App Distribution Router
============================================
1. /share/*                — Deep link redirect pages (open app or download)
2. /api/app/version        — Version check API (app calls on launch)
3. /download/latest        — Direct APK download (serves from backend/releases/)
4. /.well-known/assetlinks.json — Android App Links verification

Flow for shared link:
  User clicks  https://server/share/match/42
     |
     +--> App installed  --> Opens directly via Android App Links
     |
     +--> App NOT installed  --> Branded HTML page:
          - Shows match info (OG tags for WhatsApp/Telegram previews)
          - "Download CrecKStars v1.2.0" button (direct APK or Play Store)
          - "Try Opening App" fallback button

Flow for update check:
  App launches --> GET /api/app/version
     |
     +--> current_version < min_version  --> Force update alert
     +--> current_version < latest_version  --> Optional update prompt
     +--> current_version >= latest_version  --> All good
"""

import os
import glob
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from src.app.api.config import (
    APP_SCHEME, APP_PACKAGE_NAME, APP_DOWNLOAD_URL, APP_SHA256_FINGERPRINT,
    APK_DIR, get_app_version_info,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_latest_apk_path():
    """Find the latest APK file in the releases directory."""
    apk_files = glob.glob(os.path.join(APK_DIR, "*.apk"))
    if not apk_files:
        return None
    # Sort by modification time, newest first
    apk_files.sort(key=os.path.getmtime, reverse=True)
    return apk_files[0]


def _get_download_url(request: Request) -> str:
    """Get the best download URL — Play Store if configured, else direct APK."""
    if APP_DOWNLOAD_URL:
        return APP_DOWNLOAD_URL
    # Fall back to direct APK download from this server
    base = str(request.base_url).rstrip("/")
    return f"{base}/download/latest"


# ─── App Version API ─────────────────────────────────────────────────────────

@router.get("/api/app/version")
async def app_version(request: Request):
    """Version check API — the app calls this on launch to check for updates.

    Reads from backend/releases/version.json (live, no restart needed).

    Response:
      latest_version: "1.2.0"
      latest_version_code: 3
      min_version: "1.0.0"       (force update if app version < this)
      release_notes: "..."
      download_url: "https://..."
      update_available: true/false  (caller sends ?current=1.0.0)
      force_update: true/false
    """
    info = get_app_version_info()
    current = request.query_params.get("current", "")
    download_url = _get_download_url(request)

    update_available = False
    force_update = False
    if current:
        try:
            current_parts = [int(x) for x in current.split(".")]
            latest_parts = [int(x) for x in info["latest_version"].split(".")]
            min_parts = [int(x) for x in info["min_version"].split(".")]
            update_available = current_parts < latest_parts
            force_update = current_parts < min_parts
        except (ValueError, AttributeError):
            pass

    return {
        "latest_version": info["latest_version"],
        "latest_version_code": info["latest_version_code"],
        "min_version": info["min_version"],
        "release_notes": info["release_notes"],
        "download_url": download_url,
        "update_available": update_available,
        "force_update": force_update,
    }


# ─── Direct APK Download ─────────────────────────────────────────────────────

@router.get("/download/latest")
async def download_latest_apk():
    """Serve the latest APK file from backend/releases/ directory.
    Place your APK in backend/releases/ — newest file by modification time is served."""
    apk_path = _get_latest_apk_path()
    if not apk_path:
        return JSONResponse(
            status_code=404,
            content={"detail": "No APK available. Place a .apk file in the releases/ directory."},
        )
    filename = os.path.basename(apk_path)
    return FileResponse(
        path=apk_path,
        filename=filename,
        media_type="application/vnd.android.package-archive",
    )


# ─── Branded HTML template ───────────────────────────────────────────────────

def _build_redirect_html(
    title: str,
    description: str,
    deep_link_path: str,
    download_url: str,
    og_image: str = "",
) -> str:
    """Build an HTML page that opens the app or shows download/update page."""
    intent_uri = (
        f"intent://{deep_link_path}"
        f"#Intent;scheme={APP_SCHEME};package={APP_PACKAGE_NAME};end"
    )
    scheme_uri = f"{APP_SCHEME}://{deep_link_path}"
    info = get_app_version_info()
    version = info["latest_version"]
    release_notes = info["release_notes"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{title} - CrecKStars</title>

  <!-- Open Graph for rich link previews (WhatsApp, Telegram, etc.) -->
  <meta property="og:title" content="{title}"/>
  <meta property="og:description" content="{description}"/>
  <meta property="og:type" content="website"/>
  <meta property="og:image" content="{og_image or ''}"/>
  <meta property="og:site_name" content="CrecKStars"/>

  <meta name="twitter:card" content="summary"/>
  <meta name="twitter:title" content="{title}"/>
  <meta name="twitter:description" content="{description}"/>

  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: linear-gradient(135deg, #0a1628 0%, #1a2a4a 50%, #0d1f3c 100%);
      color: #fff;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .card {{
      text-align: center;
      padding: 40px 28px;
      max-width: 400px;
      width: 92%;
    }}
    .logo {{
      width: 80px; height: 80px;
      border-radius: 20px;
      background: linear-gradient(135deg, #00c6ff, #0072ff);
      margin: 0 auto 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 36px;
      box-shadow: 0 8px 32px rgba(0,114,255,0.3);
    }}
    h1 {{
      font-size: 22px;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .subtitle {{
      font-size: 14px;
      color: #8aa4c8;
      margin-bottom: 24px;
      line-height: 1.5;
    }}
    .version-badge {{
      display: inline-block;
      background: rgba(0,198,255,0.15);
      border: 1px solid rgba(0,198,255,0.3);
      color: #00c6ff;
      font-size: 12px;
      font-weight: 600;
      padding: 4px 14px;
      border-radius: 20px;
      margin-bottom: 20px;
    }}
    .release-notes {{
      font-size: 12px;
      color: #6889ab;
      margin-bottom: 24px;
      line-height: 1.6;
      background: rgba(255,255,255,0.04);
      border-radius: 10px;
      padding: 12px 16px;
      text-align: left;
    }}
    .release-notes strong {{
      color: #8aa4c8;
    }}
    .btn {{
      display: inline-block;
      padding: 14px 40px;
      border-radius: 12px;
      font-size: 16px;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      transition: transform 0.15s, box-shadow 0.15s;
      width: 100%;
      max-width: 280px;
    }}
    .btn:hover {{
      transform: translateY(-2px);
      box-shadow: 0 6px 20px rgba(0,114,255,0.4);
    }}
    .btn-primary {{
      background: linear-gradient(135deg, #00c6ff, #0072ff);
      color: #fff;
    }}
    .btn-secondary {{
      background: rgba(255,255,255,0.08);
      color: #8aa4c8;
      border: 1px solid rgba(255,255,255,0.12);
      margin-top: 12px;
      font-size: 14px;
      padding: 11px 32px;
    }}
    .btn-update {{
      background: linear-gradient(135deg, #ff9800, #f57c00);
      color: #fff;
      margin-top: 12px;
    }}
    .spinner {{
      border: 3px solid rgba(255,255,255,0.15);
      border-top-color: #00c6ff;
      border-radius: 50%;
      width: 32px; height: 32px;
      animation: spin 0.8s linear infinite;
      margin: 0 auto 16px;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    #opening {{ margin-bottom: 24px; }}
    #fallback {{ display: none; }}
    .footer {{ font-size: 11px; color: #3d5a80; margin-top: 28px; }}
    .footer a {{ color: #5a7a9a; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">&#127951;</div>

    <!-- Phase 1: Trying to open app -->
    <div id="opening">
      <div class="spinner"></div>
      <p class="subtitle">Opening CrecKStars...</p>
    </div>

    <!-- Phase 2: App not found — show download / update page -->
    <div id="fallback">
      <h1>{title}</h1>
      <p class="subtitle">{description}</p>

      <div class="version-badge">v{version} Latest</div>

      <div class="release-notes">
        <strong>What's new:</strong><br/>
        {release_notes}
      </div>

      <a href="{download_url}" class="btn btn-primary" id="downloadBtn">
        Download CrecKStars v{version}
      </a>
      <br/>
      <a href="{scheme_uri}" class="btn btn-secondary">
        Open in App
      </a>

      <p class="footer">
        CrecKStars &mdash; Live cricket scoring, tournaments & community
      </p>
    </div>
  </div>

  <script>
    var intentUri = "{intent_uri}";
    var schemeUri = "{scheme_uri}";
    var isAndroid = /android/i.test(navigator.userAgent);

    // Attempt to open the app
    if (isAndroid) {{
      window.location.href = intentUri;
    }} else {{
      window.location.href = schemeUri;
    }}

    // If still here after 1.5s, app is not installed — show fallback
    setTimeout(function() {{
      document.getElementById('opening').style.display = 'none';
      document.getElementById('fallback').style.display = 'block';
    }}, 1500);
  </script>
</body>
</html>"""


# ─── Share routes ─────────────────────────────────────────────────────────────

@router.get("/share/match/{match_id}", response_class=HTMLResponse)
async def share_match(request: Request, match_id: int):
    title = f"Match #{match_id}"
    description = "Tap to view live score, scorecard & ball-by-ball commentary on CrecKStars"
    try:
        from src.database.postgres.db import db
        async with db.AsyncSessionLocal() as session:
            from src.database.postgres.repositories.match_repository import MatchRepository
            from src.database.postgres.schemas.team_schema import TeamSchema
            match = await MatchRepository.get_by_id(session, match_id)
            if match:
                ta = tb = None
                if match.team_a_id:
                    ta = await session.get(TeamSchema, match.team_a_id)
                if match.team_b_id:
                    tb = await session.get(TeamSchema, match.team_b_id)
                ta_name = ta.name if ta else "Team A"
                tb_name = tb.name if tb else "Team B"
                title = f"{ta_name} vs {tb_name}"
                if match.result_summary:
                    description = match.result_summary
                elif match.status == "live":
                    description = "LIVE now! Tap to watch ball-by-ball"
                else:
                    description = f"{match.overs}-over match on CrecKStars"
    except Exception as _e:
        logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    return _build_redirect_html(
        title=title,
        description=description,
        deep_link_path=f"match/{match_id}",
        download_url=_get_download_url(request),
    )


@router.get("/share/match/{match_id}/scorecard", response_class=HTMLResponse)
async def share_scorecard(request: Request, match_id: int):
    title = f"Scorecard - Match #{match_id}"
    description = "View full scorecard with batting, bowling & fall of wickets"
    try:
        from src.database.postgres.db import db
        async with db.AsyncSessionLocal() as session:
            from src.database.postgres.repositories.match_repository import MatchRepository
            from src.database.postgres.schemas.team_schema import TeamSchema
            match = await MatchRepository.get_by_id(session, match_id)
            if match:
                ta = tb = None
                if match.team_a_id:
                    ta = await session.get(TeamSchema, match.team_a_id)
                if match.team_b_id:
                    tb = await session.get(TeamSchema, match.team_b_id)
                ta_name = ta.name if ta else "Team A"
                tb_name = tb.name if tb else "Team B"
                title = f"{ta_name} vs {tb_name} - Scorecard"
    except Exception as _e:
        logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    return _build_redirect_html(
        title=title,
        description=description,
        deep_link_path=f"match/{match_id}/scorecard",
        download_url=_get_download_url(request),
    )


@router.get("/share/tournament/{tournament_id}", response_class=HTMLResponse)
async def share_tournament(request: Request, tournament_id: int):
    title = f"Tournament #{tournament_id}"
    description = "View standings, fixtures & results on CrecKStars"
    try:
        from src.database.postgres.db import db
        async with db.AsyncSessionLocal() as session:
            from src.database.postgres.repositories.tournament_repository import TournamentRepository
            t = await TournamentRepository.get_by_id(session, tournament_id)
            if t:
                title = t.name or title
                description = f"{t.name} - View standings, fixtures & results"
    except Exception as _e:
        logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    return _build_redirect_html(
        title=title,
        description=description,
        deep_link_path=f"tournament/{tournament_id}",
        download_url=_get_download_url(request),
    )


@router.get("/share/team/{team_id}", response_class=HTMLResponse)
async def share_team(request: Request, team_id: int):
    return _build_redirect_html(
        title="Team Profile",
        description="View team roster, stats & match history on CrecKStars",
        deep_link_path=f"team/{team_id}",
        download_url=_get_download_url(request),
    )


@router.get("/share/player/{player_id}", response_class=HTMLResponse)
async def share_player(request: Request, player_id: int):
    return _build_redirect_html(
        title="Player Profile",
        description="View batting & bowling stats on CrecKStars",
        deep_link_path=f"player/{player_id}",
        download_url=_get_download_url(request),
    )


# ─── Android App Links verification ──────────────────────────────────────────

@router.get("/.well-known/assetlinks.json")
async def assetlinks():
    fingerprint = APP_SHA256_FINGERPRINT or "__ADD_YOUR_SHA256_FINGERPRINT__"
    return JSONResponse(
        content=[{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": APP_PACKAGE_NAME,
                "sha256_cert_fingerprints": [fingerprint],
            },
        }],
        headers={"Content-Type": "application/json"},
    )
