import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send
from src.database.postgres.db import db, Base
from starlette.middleware.gzip import GZipMiddleware
from src.app.api.routers import main_router
from src.app.api.config import CORS_ORIGINS, validate_config
from src.app.api.rate_limiter import limiter, rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from src.utils.logger import request_id_var

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "uploads")

# Import schemas so they are registered with Base
import src.database.postgres.schemas.user_schema  # noqa
import src.database.postgres.schemas.otp_schema  # noqa
import src.database.postgres.schemas.team_schema  # noqa
import src.database.postgres.schemas.player_schema  # noqa
import src.database.postgres.schemas.team_player_schema  # noqa
import src.database.postgres.schemas.venue_schema  # noqa
import src.database.postgres.schemas.tournament_schema  # noqa
import src.database.postgres.schemas.tournament_team_schema  # noqa
import src.database.postgres.schemas.match_schema  # noqa
import src.database.postgres.schemas.match_squad_schema  # noqa
import src.database.postgres.schemas.innings_schema  # noqa
import src.database.postgres.schemas.over_schema  # noqa
import src.database.postgres.schemas.delivery_schema  # noqa
import src.database.postgres.schemas.batting_scorecard_schema  # noqa
import src.database.postgres.schemas.bowling_scorecard_schema  # noqa
import src.database.postgres.schemas.fall_of_wicket_schema  # noqa
import src.database.postgres.schemas.partnership_schema  # noqa
import src.database.postgres.schemas.match_event_schema  # noqa
import src.database.postgres.schemas.post_schema  # noqa
import src.database.postgres.schemas.tournament_stage_schema  # noqa
import src.database.postgres.schemas.tournament_group_schema  # noqa
import src.database.postgres.schemas.tournament_group_team_schema  # noqa
import src.database.postgres.schemas.push_token_schema  # noqa


CORS_HEADERS = {
    "access-control-allow-origin": CORS_ORIGINS,
    "access-control-allow-methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
    "access-control-allow-headers": "content-type, authorization, x-requested-with, accept, origin",
    "access-control-max-age": "86400",
}

# Pre-encode CORS headers as bytes at module level to avoid per-request encoding
CORS_HEADERS_ENCODED = [
    (k.encode(), v.encode()) for k, v in CORS_HEADERS.items()
]


class RequestTracingMiddleware:
    """Injects X-Request-ID into every request for end-to-end log correlation."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Use client-provided request ID or generate one
        headers = dict(scope.get("headers", []))
        req_id = headers.get(b"x-request-id", b"").decode() or uuid.uuid4().hex[:16]

        # Set context var for structured logging
        token = request_id_var.set(req_id)

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                new_headers = list(message.get("headers", []))
                new_headers.append((b"x-request-id", req_id.encode()))
                message = {**message, "headers": new_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)


class CORSMiddleware:
    """Pure ASGI CORS middleware — no BaseHTTPMiddleware, no Starlette CORSMiddleware.
    This avoids all known issues with header duplication and streaming."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Handle preflight
        method = scope.get("method", "")
        if method == "OPTIONS":
            response = Response(status_code=200, headers=CORS_HEADERS)
            await response(scope, receive, send)
            return

        # For normal requests, inject CORS headers into the response
        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                # Add CORS headers
                new_headers = list(message.get("headers", []))
                new_headers.extend(CORS_HEADERS_ENCODED)
                message = {**message, "headers": new_headers}
            await send(message)

        await self.app(scope, receive, send_with_cors)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[STARTUP] Lifespan starting...")
    # Schema managed via schema.sql — no migrations on startup
    try:
        validate_config()
        print("[CONFIG] Validated")
    except Exception as e:
        print(f"[CONFIG] FATAL: {e}")
        raise
    # Start Redis Pub/Sub subscriber for WebSocket multi-instance support
    try:
        from src.services.websocket_service import ws_manager
        await ws_manager.start_subscriber()
        print("[WS] Subscriber started")
    except Exception as e:
        print(f"[WS] Subscriber failed (non-fatal): {e}")
    # Start notification worker (Observer on same Redis event bus)
    try:
        from src.services.notification_service import notification_worker
        await notification_worker.start()
        print("[Notifications] Worker started")
    except Exception as e:
        print(f"[Notifications] Worker failed (non-fatal): {e}")
    yield
    try:
        await notification_worker.stop()
    except Exception:
        pass
    await db.async_engine.dispose()


try:
    from fastapi.responses import ORJSONResponse
    default_response = ORJSONResponse
except ImportError:
    default_response = None

app = FastAPI(
    title="CreckStars API",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=default_response or None,
)

# GZip compression — 60-70% smaller responses
app.add_middleware(GZipMiddleware, minimum_size=500)

# Single pure-ASGI CORS middleware — no conflicts
app.add_middleware(CORSMiddleware)

# Request tracing — adds X-Request-ID to every request/response for log correlation
app.add_middleware(RequestTracingMiddleware)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Global exception handler — logs traceback + returns error detail for debugging
import traceback as _tb
from fastapi.responses import JSONResponse as _JSONResp
from src.utils.logger import get_logger as _get_logger
_err_logger = _get_logger("unhandled")

@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):
    tb_str = _tb.format_exc()
    _err_logger.error(f"Unhandled {type(exc).__name__} on {request.method} {request.url.path}: {exc}\n{tb_str}")
    return _JSONResp(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {str(exc)}",
            "path": str(request.url.path),
        },
    )

app.include_router(main_router)

# Serve uploaded files
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

# Serve APK releases
from src.app.api.config import APK_DIR
os.makedirs(APK_DIR, exist_ok=True)


@app.get("/", response_class=HTMLResponse)
async def root():
    """Landing page — shareable link to download the app."""
    from fastapi.responses import HTMLResponse
    from src.app.api.config import get_app_version_info
    info = get_app_version_info()
    v = info["latest_version"]
    notes = info["release_notes"]
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>CrecKStars · Cricket Scoring App</title>
<meta name="description" content="Score live cricket matches ball-by-ball, organize tournaments, build teams, and connect with the cricket community."/>
<meta property="og:title" content="CrecKStars · Cricket Scoring App"/>
<meta property="og:description" content="Score live matches, organize tournaments, track your stats."/>
<meta property="og:type" content="website"/>
<meta property="og:site_name" content="CrecKStars"/>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0D0D0D;color:#fff;min-height:100vh}}
.hero{{text-align:center;padding:60px 24px 40px;background:linear-gradient(180deg,#0A1628 0%,#0D0D0D 100%)}}
.logo{{width:100px;height:100px;border-radius:28px;background:linear-gradient(135deg,#1E88E5,#42A5F5);margin:0 auto 24px;display:flex;align-items:center;justify-content:center;font-size:52px;box-shadow:0 12px 40px rgba(30,136,229,0.4)}}
h1{{font-size:36px;font-weight:900;letter-spacing:-1px;margin-bottom:8px}}
h1 span{{color:#1E88E5}}
.tagline{{font-size:16px;color:#777;font-weight:500;margin-bottom:32px}}
.dl-btn{{display:inline-block;background:#1E88E5;color:#fff;font-size:17px;font-weight:700;padding:16px 48px;border-radius:14px;text-decoration:none;box-shadow:0 8px 24px rgba(30,136,229,0.4);transition:transform .15s}}
.dl-btn:hover{{transform:translateY(-2px)}}
.ver{{display:inline-block;margin-top:14px;font-size:12px;color:#555;background:#1A1A1A;padding:5px 14px;border-radius:20px;border:1px solid #2C2C2C}}

.features{{max-width:600px;margin:0 auto;padding:40px 24px}}
.features h2{{font-size:20px;font-weight:800;margin-bottom:24px;text-align:center;color:#B0B0B0}}
.feat{{display:flex;gap:16px;align-items:flex-start;margin-bottom:20px;background:#1A1A1A;padding:16px;border-radius:14px;border:1px solid #2C2C2C}}
.feat .ic{{width:44px;height:44px;border-radius:12px;background:rgba(30,136,229,0.15);display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}}
.feat h3{{font-size:14px;font-weight:700;color:#fff;margin-bottom:4px}}
.feat p{{font-size:12px;color:#777;line-height:1.4}}

.notes{{max-width:600px;margin:0 auto;padding:0 24px 20px}}
.notes-card{{background:#1A1A1A;border:1px solid #2C2C2C;border-radius:14px;padding:16px}}
.notes-card h3{{font-size:13px;font-weight:700;color:#555;margin-bottom:8px;text-transform:uppercase;letter-spacing:1px}}
.notes-card p{{font-size:13px;color:#777;line-height:1.5}}

.footer{{text-align:center;padding:40px 24px;font-size:11px;color:#333}}
.footer a{{color:#1E88E5;text-decoration:none}}
</style>
</head>
<body>
<div class="hero">
  <div class="logo">&#127951;</div>
  <h1>Crec<span>K</span>Stars</h1>
  <p class="tagline">Score · Track · Win</p>
  <a href="/download/latest" class="dl-btn">Download APK</a>
  <br/>
  <span class="ver">v{v} · Latest</span>
</div>

<div class="features">
  <h2>Everything you need for cricket</h2>
  <div class="feat">
    <div class="ic">&#127951;</div>
    <div><h3>Live Ball-by-Ball Scoring</h3><p>Score matches in real-time with detailed ball tracking, extras, wickets, and automatic run rate calculation.</p></div>
  </div>
  <div class="feat">
    <div class="ic">&#127942;</div>
    <div><h3>Tournament Management</h3><p>Create league + knockout tournaments. Multi-group round-robin, auto-progression, standings, and leaderboards.</p></div>
  </div>
  <div class="feat">
    <div class="ic">&#128101;</div>
    <div><h3>Teams & Players</h3><p>Build squads, track player career stats across tournaments, batting and bowling averages.</p></div>
  </div>
  <div class="feat">
    <div class="ic">&#128172;</div>
    <div><h3>Community</h3><p>Share posts, create polls, discuss matches with the cricket community. Trending hashtags and feeds.</p></div>
  </div>
  <div class="feat">
    <div class="ic">&#128202;</div>
    <div><h3>Stats & Leaderboards</h3><p>Orange Cap, Purple Cap, NRR standings, best bowling figures — all computed automatically.</p></div>
  </div>
</div>

<div class="notes">
  <div class="notes-card">
    <h3>What's New in v{v}</h3>
    <p>{notes}</p>
  </div>
</div>

<div class="footer">
  CrecKStars &copy; 2025 &middot; <a href="/health">API Status</a> &middot; <a href="/api/app/version">Version API</a>
</div>
</body>
</html>""")

@app.get("/api")
def api_root():
    return {"message": "CreckStars API is running"}


_health_cache = {"data": None, "ts": 0}

@app.get("/health")
async def health():
    """Health check for load balancers and monitoring.
    Cached for 3s to avoid DB+Redis pings on every poll cycle."""
    import time as _t
    now = _t.time()
    if _health_cache["data"] and now - _health_cache["ts"] < 3:
        return _health_cache["data"]

    db_ok = False
    redis_ok = False
    try:
        from sqlalchemy import text
        async with db.AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass
    try:
        from src.database.redis.redis_client import redis_client
        r = await redis_client.get_client()
        if r:
            await r.ping()
            redis_ok = True
    except Exception:
        pass

    ws_stats = {}
    try:
        from src.services.websocket_service import ws_manager
        ws_stats = {
            "total_connections": ws_manager.get_total_connections(),
            "active_matches": len(ws_manager.get_active_match_ids()),
        }
    except Exception:
        pass

    health_status = "ok" if db_ok and redis_ok else "degraded"
    result = {"status": health_status, "db": db_ok, "redis": redis_ok, "websocket": ws_stats}
    _health_cache["data"] = result
    _health_cache["ts"] = now
    return result
