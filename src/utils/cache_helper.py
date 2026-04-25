"""Reusable Redis cache helper — eliminates duplicate boilerplate across routers/services."""
import json
from functools import wraps
from src.database.redis.redis_client import redis_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def _get_redis():
    try:
        return await redis_client.get_client()
    except Exception:
        return None


async def cache_get(key: str):
    """Read from Redis. Returns parsed JSON or None. Never raises."""
    r = await _get_redis()
    if not r:
        return None
    try:
        data = await r.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        logger.warning(f"Cache read failed for {key}: {e}")
    return None


async def cache_set(key: str, value, ttl: int = 60):
    """Write to Redis. Silent on failure. Pass None to delete."""
    r = await _get_redis()
    if not r:
        return
    try:
        if value is None:
            await r.delete(key)
        else:
            await r.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.warning(f"Cache write failed for {key}: {e}")


async def cache_delete(*keys: str):
    """Delete one or more Redis keys. Silent on failure."""
    r = await _get_redis()
    if not r:
        return
    try:
        await r.delete(*keys)
    except Exception as e:
        logger.warning(f"Cache delete failed for {keys}: {e}")


def cached_async(key_fn, ttl: int = 60):
    """Decorator for async functions. Caches return value in Redis."""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            cached = await cache_get(key)
            if cached is not None:
                return cached
            result = await fn(*args, **kwargs)
            await cache_set(key, result, ttl)
            return result
        wrapper.invalidate = lambda *a, **kw: cache_delete(key_fn(*a, **kw))
        return wrapper
    return decorator
