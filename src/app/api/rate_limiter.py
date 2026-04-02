from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse
from starlette.requests import Request
from src.app.api.config import REDIS_URL, RATE_LIMIT_DEFAULT


def _get_real_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For (behind load balancer) or fall back to direct IP."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "127.0.0.1"


def _get_user_or_ip(request: Request) -> str:
    """Use authenticated user ID if available, else fall back to IP.
    This prevents all users behind the same NAT/proxy from sharing a single limit."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            from jose import jwt
            from src.app.api.config import SECRET_KEY, ALGORITHM
            token = auth.split(" ")[1]
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except Exception:
            pass
    return _get_real_ip(request)


# Use Redis for rate limiting if available, fallback to in-memory
_storage_uri = REDIS_URL if REDIS_URL else "memory://"
_storage_options = {}
# Test Redis connectivity for rate limiter (sync client)
if _storage_uri and _storage_uri.startswith("redis"):
    try:
        import redis as _sync_redis
        _test = _sync_redis.from_url(_storage_uri, socket_timeout=2, ssl_cert_reqs=None)
        _test.ping()
        _test.close()
        if _storage_uri.startswith("rediss://"):
            _storage_options = {"ssl_cert_reqs": "none"}
    except Exception:
        print("[RATE LIMITER] Redis unavailable, using in-memory storage")
        _storage_uri = "memory://"
        _storage_options = {}

limiter = Limiter(
    key_func=_get_user_or_ip,
    storage_uri=_storage_uri,
    storage_options=_storage_options,
    default_limits=[RATE_LIMIT_DEFAULT],
)


async def rate_limit_exceeded_handler(request, exc):
    retry_after = getattr(exc, "retry_after", 60)
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Too many requests. Please try again later.",
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )
