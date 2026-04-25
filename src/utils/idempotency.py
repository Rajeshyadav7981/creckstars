"""Idempotency-Key support backed by Redis for at-most-once semantics on mutating requests."""
from __future__ import annotations

import hashlib
import json
from functools import wraps
from typing import Callable

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Default TTL (1 hour) — long enough to cover mobile retry loops / flaky networks,
# short enough that users eventually see fresh behavior after editing & resubmitting.
DEFAULT_TTL_SECONDS = 3600

# Max idempotency-key length we'll accept (prevent DOS via giant keys).
MAX_KEY_LEN = 128


def _cache_key(user_id: int, path: str, key: str) -> str:
    # Hash the (path, key) pair so Redis keys are bounded-length regardless of path.
    h = hashlib.sha256(f"{path}|{key}".encode()).hexdigest()[:24]
    return f"idem:{user_id}:{h}"


def idempotent(ttl_seconds: int = DEFAULT_TTL_SECONDS):
    """Decorator: if the client sends Idempotency-Key, cache the 2xx response."""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Request | None = kwargs.get("request")
            user = kwargs.get("user")
            if request is None or user is None:
                # Handler signature missing expected kwargs — skip idempotency rather
                # than silently pass through; easier to spot in dev.
                return await func(*args, **kwargs)

            key = request.headers.get("idempotency-key") or request.headers.get("Idempotency-Key")
            if not key:
                return await func(*args, **kwargs)
            if len(key) > MAX_KEY_LEN:
                raise HTTPException(status_code=400, detail="Idempotency-Key too long")

            path = str(request.url.path)
            cache_key = _cache_key(user.id, path, key)

            # Lazy import so this module doesn't hard-depend on Redis at import time.
            try:
                from src.database.redis.redis_client import redis_client
                r = await redis_client.get_client()
            except Exception:
                r = None

            if r is not None:
                try:
                    cached = await r.get(cache_key)
                    if cached:
                        payload = json.loads(cached)
                        return JSONResponse(
                            status_code=payload.get("status", 200),
                            content=payload.get("body"),
                            headers={"Idempotent-Replay": "true"},
                        )
                except Exception as e:
                    logger.warning(f"Idempotency cache read failed: {e}")

            try:
                result = await func(*args, **kwargs)
            except HTTPException:
                # Client-visible errors bubble up; do NOT cache them so user can retry
                # after fixing input.
                raise

            if r is not None:
                try:
                    if hasattr(result, "body") and hasattr(result, "status_code"):
                        # Already a Response — skip caching to avoid double-serialization.
                        pass
                    else:
                        await r.setex(
                            cache_key,
                            ttl_seconds,
                            json.dumps({"status": 200, "body": result}, default=str),
                        )
                except Exception as e:
                    logger.warning(f"Idempotency cache write failed: {e}")

            return result

        return wrapper

    return decorator
