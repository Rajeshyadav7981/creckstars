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
<title>CrecKStars</title>
<meta name="description" content="Live cricket scoring, tournaments, teams & community"/>
<meta property="og:title" content="CrecKStars"/>
<meta property="og:description" content="Score live matches, organize tournaments, track your stats."/>
<meta property="og:type" content="website"/>
<meta property="og:site_name" content="CrecKStars"/>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'SF Pro Display',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#000;color:#fff;min-height:100vh;overflow-x:hidden}}

.page{{max-width:420px;margin:0 auto;padding:0 20px}}

/* Hero */
.hero{{padding:72px 0 48px;text-align:center;position:relative}}
.hero::before{{content:'';position:absolute;top:-40px;left:50%;transform:translateX(-50%);width:300px;height:300px;background:radial-gradient(circle,rgba(30,136,229,0.12) 0%,transparent 70%);pointer-events:none}}
.logo{{width:72px;height:72px;border-radius:18px;background:#1E88E5;margin:0 auto 20px;display:flex;align-items:center;justify-content:center;font-size:36px}}
.name{{font-size:28px;font-weight:800;letter-spacing:-.5px}}
.name span{{color:#1E88E5}}
.tag{{font-size:14px;color:#666;margin-top:6px;font-weight:500;letter-spacing:.5px}}

/* Download */
.dl{{margin:36px 0 0;text-align:center}}
.dl a{{display:inline-flex;align-items:center;gap:10px;background:#1E88E5;color:#fff;font-size:15px;font-weight:700;padding:14px 36px;border-radius:50px;text-decoration:none;transition:all .2s}}
.dl a:hover{{background:#1976D2;transform:scale(1.02)}}
.dl a svg{{width:20px;height:20px;fill:#fff}}
.dl .v{{display:block;margin-top:12px;font-size:11px;color:#444;font-weight:600;letter-spacing:.5px}}

/* Stats strip */
.stats{{display:flex;gap:0;margin:40px 0 0;border-radius:14px;overflow:hidden;border:1px solid #1a1a1a}}
.stat{{flex:1;padding:16px 8px;text-align:center;background:#0a0a0a}}
.stat+.stat{{border-left:1px solid #1a1a1a}}
.stat .n{{font-size:20px;font-weight:800;color:#fff}}
.stat .l{{font-size:9px;color:#555;font-weight:700;text-transform:uppercase;letter-spacing:.8px;margin-top:4px}}

/* Features */
.feats{{margin:40px 0 0}}
.feats h2{{font-size:11px;font-weight:700;color:#444;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:16px}}
.f{{display:flex;align-items:center;gap:14px;padding:14px 0;border-bottom:1px solid #111}}
.f:last-child{{border-bottom:none}}
.f .ic{{width:36px;height:36px;border-radius:10px;background:#111;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}}
.f .t{{font-size:13px;font-weight:600;color:#ccc}}
.f .d{{font-size:11px;color:#555;margin-top:2px}}

/* What's new */
.new{{margin:32px 0 0;padding:16px;background:#0a0a0a;border-radius:12px;border:1px solid #1a1a1a}}
.new h3{{font-size:10px;font-weight:700;color:#1E88E5;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:8px}}
.new p{{font-size:12px;color:#555;line-height:1.5}}

/* Footer */
.foot{{text-align:center;padding:48px 0 32px;font-size:10px;color:#333}}
</style>
</head>
<body>
<div class="page">

<div class="hero">
  <div class="logo">&#127951;</div>
  <div class="name">Crec<span>K</span>Stars</div>
  <div class="tag">Score &middot; Track &middot; Win</div>
</div>

<div class="dl">
  <a href="/download/latest">
    <svg viewBox="0 0 24 24"><path d="M5 20h14v-2H5v2zm7-18l-7 7h4v6h6v-6h4l-7-7z"/></svg>
    Download v{v}
  </a>
  <span class="v">Android &middot; {notes.split('.')[0] if '.' in notes else notes}</span>
</div>

<div class="stats">
  <div class="stat"><div class="n">&#127951;</div><div class="l">Live Scoring</div></div>
  <div class="stat"><div class="n">&#127942;</div><div class="l">Tournaments</div></div>
  <div class="stat"><div class="n">&#128202;</div><div class="l">Stats</div></div>
</div>

<div class="feats">
  <h2>Features</h2>
  <div class="f"><div class="ic">&#127951;</div><div><div class="t">Ball-by-Ball Scoring</div><div class="d">Real-time scoring with extras, wickets & run rates</div></div></div>
  <div class="f"><div class="ic">&#127942;</div><div><div class="t">Tournaments</div><div class="d">League + knockout with auto-progression & standings</div></div></div>
  <div class="f"><div class="ic">&#128101;</div><div><div class="t">Teams & Squads</div><div class="d">Build teams, track player career stats</div></div></div>
  <div class="f"><div class="ic">&#128172;</div><div><div class="t">Community</div><div class="d">Posts, polls & cricket discussions</div></div></div>
  <div class="f"><div class="ic">&#128202;</div><div><div class="t">Leaderboards</div><div class="d">Orange Cap, Purple Cap, NRR & more</div></div></div>
</div>

<div class="new">
  <h3>v{v}</h3>
  <p>{notes}</p>
</div>

<div class="foot">CrecKStars &copy; 2025</div>

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
