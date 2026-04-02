import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
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

app.include_router(main_router)

# Serve uploaded files
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

# Serve APK releases
from src.app.api.config import APK_DIR
os.makedirs(APK_DIR, exist_ok=True)


@app.get("/")
def root():
    return {"message": "CreckStars API is running"}


@app.get("/health")
async def health():
    """Health check for load balancers and monitoring."""
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

    # WebSocket connection stats
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
    return {"status": health_status, "db": db_ok, "redis": redis_ok, "websocket": ws_stats}
