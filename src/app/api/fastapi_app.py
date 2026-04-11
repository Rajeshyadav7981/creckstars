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
    short_note = notes.split('.')[0] if '.' in notes else notes
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>CrecKStars &mdash; Live Cricket Scoring &amp; Tournaments</title>
<meta name="description" content="CrecKStars is the all-in-one cricket app for live ball-by-ball scoring, tournament management, team stats, leaderboards and community. Free Android app."/>
<meta name="theme-color" content="#ffffff"/>
<meta property="og:title" content="CrecKStars &mdash; Live Cricket Scoring"/>
<meta property="og:description" content="Score live matches, run tournaments, track every run & wicket."/>
<meta property="og:type" content="website"/>
<meta property="og:site_name" content="CrecKStars"/>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{font-family:'SF Pro Display',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#F7F9FC;color:#0F172A;min-height:100vh;overflow-x:hidden;-webkit-font-smoothing:antialiased}}

.wrap{{max-width:1080px;margin:0 auto;padding:0 24px}}

/* Nav */
.nav{{display:flex;align-items:center;justify-content:space-between;padding:22px 0}}
.brand{{display:flex;align-items:center;gap:10px;font-size:18px;font-weight:800;letter-spacing:-.3px;color:#0F172A;text-decoration:none}}
.brand .mark{{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#1E88E5 0%,#42A5F5 100%);display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 6px 18px rgba(30,136,229,.25)}}
.brand span{{color:#1E88E5}}
.nav .links{{display:flex;gap:28px;font-size:14px;font-weight:500}}
.nav .links a{{color:#475569;text-decoration:none;transition:color .15s}}
.nav .links a:hover{{color:#1E88E5}}
.nav .cta{{background:#1E88E5;color:#fff;padding:9px 18px;border-radius:100px;font-size:13px;font-weight:700;text-decoration:none;box-shadow:0 4px 14px rgba(30,136,229,.3);transition:transform .15s}}
.nav .cta:hover{{transform:translateY(-1px)}}
@media(max-width:720px){{.nav .links{{display:none}}}}

/* Hero */
.hero{{padding:60px 0 80px;text-align:center;position:relative}}
.hero::before{{content:'';position:absolute;top:0;left:50%;transform:translateX(-50%);width:720px;height:480px;background:radial-gradient(ellipse at center,rgba(30,136,229,.10) 0%,transparent 60%);pointer-events:none;z-index:0}}
.hero > *{{position:relative;z-index:1}}
.badge{{display:inline-flex;align-items:center;gap:8px;background:#fff;border:1px solid #E2E8F0;color:#1E88E5;font-size:12px;font-weight:700;padding:8px 16px;border-radius:100px;margin-bottom:24px;box-shadow:0 4px 12px rgba(15,23,42,.04)}}
.badge .dot{{width:6px;height:6px;border-radius:50%;background:#22C55E;box-shadow:0 0 0 3px rgba(34,197,94,.2)}}
.hero h1{{font-size:52px;font-weight:800;letter-spacing:-1.5px;line-height:1.05;color:#0F172A;margin-bottom:20px}}
.hero h1 .hl{{color:#1E88E5}}
.hero p.sub{{font-size:18px;color:#475569;max-width:600px;margin:0 auto 36px;line-height:1.6}}
.hero .ctas{{display:flex;gap:14px;justify-content:center;flex-wrap:wrap}}
.hero .btn-primary{{display:inline-flex;align-items:center;gap:10px;background:#1E88E5;color:#fff;font-size:15px;font-weight:700;padding:16px 32px;border-radius:100px;text-decoration:none;box-shadow:0 10px 30px rgba(30,136,229,.35);transition:all .2s}}
.hero .btn-primary:hover{{background:#1976D2;transform:translateY(-2px);box-shadow:0 14px 36px rgba(30,136,229,.4)}}
.hero .btn-primary svg{{width:20px;height:20px;fill:#fff}}
.hero .btn-secondary{{display:inline-flex;align-items:center;gap:8px;background:#fff;color:#0F172A;font-size:14px;font-weight:600;padding:16px 24px;border-radius:100px;text-decoration:none;border:1px solid #E2E8F0;transition:all .2s}}
.hero .btn-secondary:hover{{border-color:#1E88E5;color:#1E88E5}}
.hero .meta{{margin-top:20px;font-size:12px;color:#64748B;font-weight:500}}
.hero .meta b{{color:#0F172A}}
@media(max-width:720px){{.hero h1{{font-size:36px}}.hero p.sub{{font-size:16px}}}}

/* Stat strip */
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:0 0 80px}}
.stat{{background:#fff;border:1px solid #E2E8F0;border-radius:16px;padding:24px 16px;text-align:center;box-shadow:0 4px 12px rgba(15,23,42,.03)}}
.stat .n{{font-size:28px;font-weight:800;color:#0F172A;letter-spacing:-.5px}}
.stat .n span{{color:#1E88E5}}
.stat .l{{font-size:11px;color:#64748B;font-weight:700;text-transform:uppercase;letter-spacing:.8px;margin-top:6px}}
@media(max-width:720px){{.stats{{grid-template-columns:repeat(2,1fr)}}}}

/* Section heads */
.sec{{margin:0 0 80px}}
.sec-head{{text-align:center;margin-bottom:48px}}
.sec-head .eyebrow{{display:inline-block;font-size:12px;font-weight:700;color:#1E88E5;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:12px}}
.sec-head h2{{font-size:36px;font-weight:800;color:#0F172A;letter-spacing:-1px;line-height:1.15}}
.sec-head p{{font-size:16px;color:#64748B;margin-top:12px;max-width:560px;margin-left:auto;margin-right:auto;line-height:1.6}}
@media(max-width:720px){{.sec-head h2{{font-size:28px}}}}

/* Features grid */
.feats{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}}
.f{{background:#fff;border:1px solid #E2E8F0;border-radius:18px;padding:28px 24px;transition:all .2s}}
.f:hover{{border-color:#1E88E5;transform:translateY(-4px);box-shadow:0 20px 40px rgba(15,23,42,.08)}}
.f .ic{{width:48px;height:48px;border-radius:12px;background:linear-gradient(135deg,#E3F2FD 0%,#BBDEFB 100%);display:flex;align-items:center;justify-content:center;font-size:22px;margin-bottom:18px}}
.f .t{{font-size:17px;font-weight:700;color:#0F172A;margin-bottom:8px}}
.f .d{{font-size:14px;color:#64748B;line-height:1.6}}
@media(max-width:900px){{.feats{{grid-template-columns:repeat(2,1fr)}}}}
@media(max-width:600px){{.feats{{grid-template-columns:1fr}}}}

/* How it works */
.steps{{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;position:relative}}
.step{{background:#fff;border:1px solid #E2E8F0;border-radius:18px;padding:32px 24px;text-align:center;position:relative}}
.step .num{{width:44px;height:44px;border-radius:50%;background:#1E88E5;color:#fff;font-size:18px;font-weight:800;display:flex;align-items:center;justify-content:center;margin:0 auto 18px;box-shadow:0 8px 20px rgba(30,136,229,.3)}}
.step .t{{font-size:17px;font-weight:700;color:#0F172A;margin-bottom:8px}}
.step .d{{font-size:14px;color:#64748B;line-height:1.6}}
@media(max-width:720px){{.steps{{grid-template-columns:1fr}}}}

/* Who's it for */
.who{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}
.who .w{{background:#fff;border:1px solid #E2E8F0;border-radius:16px;padding:24px 18px;text-align:center}}
.who .w .ic{{font-size:32px;margin-bottom:10px}}
.who .w .t{{font-size:14px;font-weight:700;color:#0F172A}}
.who .w .d{{font-size:12px;color:#64748B;margin-top:4px;line-height:1.5}}
@media(max-width:720px){{.who{{grid-template-columns:repeat(2,1fr)}}}}

/* FAQ */
.faq{{max-width:720px;margin:0 auto}}
.q{{background:#fff;border:1px solid #E2E8F0;border-radius:14px;padding:20px 24px;margin-bottom:12px}}
.q .t{{font-size:15px;font-weight:700;color:#0F172A;margin-bottom:6px}}
.q .a{{font-size:14px;color:#64748B;line-height:1.6}}

/* What's new card */
.new{{background:linear-gradient(135deg,#1E88E5 0%,#1565C0 100%);border-radius:22px;padding:40px 32px;color:#fff;text-align:center;box-shadow:0 20px 50px rgba(30,136,229,.25);margin-bottom:80px}}
.new .tag{{display:inline-block;background:rgba(255,255,255,.2);font-size:11px;font-weight:700;padding:6px 14px;border-radius:100px;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px}}
.new h3{{font-size:24px;font-weight:800;margin-bottom:10px;letter-spacing:-.3px}}
.new p{{font-size:15px;opacity:.9;max-width:520px;margin:0 auto 22px;line-height:1.6}}
.new a{{display:inline-flex;align-items:center;gap:8px;background:#fff;color:#1E88E5;font-size:14px;font-weight:700;padding:12px 26px;border-radius:100px;text-decoration:none;transition:transform .15s}}
.new a:hover{{transform:translateY(-1px)}}

/* Footer */
.foot{{border-top:1px solid #E2E8F0;padding:40px 0;text-align:center}}
.foot .brand-sm{{font-size:14px;font-weight:700;color:#0F172A;margin-bottom:6px}}
.foot .brand-sm span{{color:#1E88E5}}
.foot .copy{{font-size:12px;color:#94A3B8}}
.foot .links{{margin-top:14px;display:flex;gap:24px;justify-content:center;font-size:12px}}
.foot .links a{{color:#64748B;text-decoration:none}}
.foot .links a:hover{{color:#1E88E5}}
</style>
</head>
<body>

<!-- Nav -->
<div class="wrap">
  <nav class="nav">
    <a href="/" class="brand"><div class="mark">&#127951;</div>Crec<span>K</span>Stars</a>
    <div class="links">
      <a href="#features">Features</a>
      <a href="#how">How it works</a>
      <a href="#who">Who it's for</a>
      <a href="#faq">FAQ</a>
    </div>
    <a href="/download/latest" class="cta">Download</a>
  </nav>
</div>

<!-- Hero -->
<div class="wrap">
  <section class="hero">
    <div class="badge"><span class="dot"></span>Version {v} &middot; Now Live</div>
    <h1>Your cricket match.<br/><span class="hl">Tracked ball-by-ball.</span></h1>
    <p class="sub">Score live matches, run full tournaments, and keep every stat of every player &mdash; all in one beautifully simple app. Built for gully cricket, club leagues and serious tournaments.</p>
    <div class="ctas">
      <a href="/download/latest" class="btn-primary">
        <svg viewBox="0 0 24 24"><path d="M5 20h14v-2H5v2zm7-18l-7 7h4v6h6v-6h4l-7-7z"/></svg>
        Download for Android
      </a>
      <a href="#features" class="btn-secondary">See features &rarr;</a>
    </div>
    <div class="meta">Free &middot; <b>Android APK</b> &middot; {short_note}</div>
  </section>
</div>

<!-- Stats -->
<div class="wrap">
  <div class="stats">
    <div class="stat"><div class="n">&#127951;</div><div class="l">Live Scoring</div></div>
    <div class="stat"><div class="n">&#127942;</div><div class="l">Tournaments</div></div>
    <div class="stat"><div class="n">&#128200;</div><div class="l">Player Stats</div></div>
    <div class="stat"><div class="n">&#128101;</div><div class="l">Community</div></div>
  </div>
</div>

<!-- Features -->
<div class="wrap">
  <section class="sec" id="features">
    <div class="sec-head">
      <span class="eyebrow">Features</span>
      <h2>Everything you need for cricket</h2>
      <p>From the first ball to the trophy lift &mdash; CrecKStars handles scoring, stats, standings and everything in between.</p>
    </div>
    <div class="feats">
      <div class="f">
        <div class="ic">&#127951;</div>
        <div class="t">Ball-by-Ball Scoring</div>
        <div class="d">Tap to score every delivery. Wides, no-balls, byes, leg-byes, wickets &mdash; all dismissal types supported with full cricket rules.</div>
      </div>
      <div class="f">
        <div class="ic">&#127942;</div>
        <div class="t">Tournaments</div>
        <div class="d">Run league stages, knockouts or hybrid formats. Auto-progression, group standings, points table and NRR calculated automatically.</div>
      </div>
      <div class="f">
        <div class="ic">&#128101;</div>
        <div class="t">Teams &amp; Squads</div>
        <div class="d">Create teams, manage squads, invite players. Career stats follow each player across every match they play.</div>
      </div>
      <div class="f">
        <div class="ic">&#128202;</div>
        <div class="t">Full Scorecards</div>
        <div class="d">Batting card, bowling figures, fall of wickets, partnerships, manhattan and worm graphs &mdash; the whole match, saved.</div>
      </div>
      <div class="f">
        <div class="ic">&#127942;</div>
        <div class="t">Leaderboards</div>
        <div class="d">Orange Cap (runs), Purple Cap (wickets), strike rate, economy, best bowling, best batting &mdash; tournament-wide rankings live.</div>
      </div>
      <div class="f">
        <div class="ic">&#128172;</div>
        <div class="t">Community</div>
        <div class="d">Share match highlights, post polls, follow friends and discuss the game. Built-in cricket social feed.</div>
      </div>
      <div class="f">
        <div class="ic">&#128225;</div>
        <div class="t">Live Updates</div>
        <div class="d">Real-time score sync via WebSocket. Everyone watching sees the same ball at the same time &mdash; no refresh needed.</div>
      </div>
      <div class="f">
        <div class="ic">&#128190;</div>
        <div class="t">Offline Scoring</div>
        <div class="d">Lost network? Keep scoring. Changes sync automatically when you&rsquo;re back online. Never miss a ball.</div>
      </div>
      <div class="f">
        <div class="ic">&#128274;</div>
        <div class="t">Secure &amp; Private</div>
        <div class="d">Encrypted connections (HTTPS), secure login with OTP, your data stays yours. No ads, no tracking.</div>
      </div>
    </div>
  </section>
</div>

<!-- How it works -->
<div class="wrap">
  <section class="sec" id="how">
    <div class="sec-head">
      <span class="eyebrow">How it works</span>
      <h2>Start scoring in 3 steps</h2>
      <p>From install to live scoring in under 2 minutes.</p>
    </div>
    <div class="steps">
      <div class="step">
        <div class="num">1</div>
        <div class="t">Download &amp; Sign up</div>
        <div class="d">Install the APK, verify your phone with OTP, and you&rsquo;re in &mdash; free forever, no credit card.</div>
      </div>
      <div class="step">
        <div class="num">2</div>
        <div class="t">Create your match</div>
        <div class="d">Pick teams, set overs, choose format (T20, ODI, custom). Add players from your saved squads or create new ones.</div>
      </div>
      <div class="step">
        <div class="num">3</div>
        <div class="t">Score live</div>
        <div class="d">Tap each delivery. Friends watch the match live on their phones. Scorecard &amp; stats update instantly.</div>
      </div>
    </div>
  </section>
</div>

<!-- Who it's for -->
<div class="wrap">
  <section class="sec" id="who">
    <div class="sec-head">
      <span class="eyebrow">Built for</span>
      <h2>Made for every cricketer</h2>
      <p>Whether it&rsquo;s a weekend gully match or a 30-team inter-society tournament &mdash; CrecKStars scales with you.</p>
    </div>
    <div class="who">
      <div class="w"><div class="ic">&#127968;</div><div class="t">Gully Cricket</div><div class="d">Score your weekend friends&rsquo; match</div></div>
      <div class="w"><div class="ic">&#127942;</div><div class="t">Club Leagues</div><div class="d">Full season, points table, NRR</div></div>
      <div class="w"><div class="ic">&#127979;</div><div class="t">Schools &amp; Colleges</div><div class="d">Inter-house &amp; campus tournaments</div></div>
      <div class="w"><div class="ic">&#127959;</div><div class="t">Corporate Leagues</div><div class="d">Office and society matches</div></div>
    </div>
  </section>
</div>

<!-- FAQ -->
<div class="wrap">
  <section class="sec" id="faq">
    <div class="sec-head">
      <span class="eyebrow">FAQ</span>
      <h2>Common questions</h2>
    </div>
    <div class="faq">
      <div class="q">
        <div class="t">Is CrecKStars free?</div>
        <div class="a">Yes &mdash; completely free. No ads, no subscriptions, no hidden costs.</div>
      </div>
      <div class="q">
        <div class="t">Is it available on iOS?</div>
        <div class="a">Currently Android only. iOS version is on the roadmap &mdash; stay tuned.</div>
      </div>
      <div class="q">
        <div class="t">Do I need the internet to score?</div>
        <div class="a">No. Scoring works offline &mdash; everything syncs the moment you reconnect. Perfect for grounds with poor signal.</div>
      </div>
      <div class="q">
        <div class="t">Can I run a full tournament?</div>
        <div class="a">Absolutely. Create tournaments with league stages, groups, knockouts, automatic standings, NRR, Orange Cap and Purple Cap rankings.</div>
      </div>
      <div class="q">
        <div class="t">Is my data safe?</div>
        <div class="a">Yes. All connections use HTTPS encryption. Your account is protected by OTP verification, and we never sell your data.</div>
      </div>
      <div class="q">
        <div class="t">How do I install the APK?</div>
        <div class="a">Tap Download, allow installs from unknown sources (Settings &rarr; Security), then open the downloaded file. Takes under 30 seconds.</div>
      </div>
    </div>
  </section>
</div>

<!-- What's new CTA -->
<div class="wrap">
  <div class="new">
    <div class="tag">What&rsquo;s new</div>
    <h3>Version {v} is here</h3>
    <p>{notes}</p>
    <a href="/download/latest">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="#1E88E5"><path d="M5 20h14v-2H5v2zm7-18l-7 7h4v6h6v-6h4l-7-7z"/></svg>
      Download v{v}
    </a>
  </div>
</div>

<!-- Footer -->
<div class="wrap">
  <footer class="foot">
    <div class="brand-sm">Crec<span>K</span>Stars</div>
    <div class="copy">&copy; 2026 CrecKStars &middot; Score &middot; Track &middot; Win</div>
    <div class="links">
      <a href="#features">Features</a>
      <a href="#how">How it works</a>
      <a href="#faq">FAQ</a>
      <a href="/download/latest">Download</a>
    </div>
  </footer>
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
