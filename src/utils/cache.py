"""
Lightweight API response cache using Redis.
Usage:
    result = await cached("matches:user:123", ttl=15, fetcher=lambda: db_query())
"""
import json
import os
from src.database.redis.redis_client import redis_client
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Bump CACHE_SCHEMA_VERSION whenever a cached payload's shape changes (e.g.
# you add/remove a field on a list or detail endpoint). The version is part
# of the key, so a deploy with the new version effectively invalidates every
# old payload — no manual flush needed, and no risk of clients reading stale
# JSON that's missing fields the new code expects.
CACHE_SCHEMA_VERSION = os.getenv("CACHE_SCHEMA_VERSION", "v2")
CACHE_PREFIX = f"api:{CACHE_SCHEMA_VERSION}:"


async def cached(key: str, ttl: int, fetcher):
    """Try Redis cache first, fall back to fetcher. Never fails — returns fetcher result on Redis error."""
    full_key = f"{CACHE_PREFIX}{key}"
    try:
        r = await redis_client.get_client()
        if r:
            data = await r.get(full_key)
            if data:
                return json.loads(data)
    except Exception as e:
        logger.warning(f"Cache read failed for {key}: {e}")

    result = await fetcher()

    try:
        r = await redis_client.get_client()
        if r:
            await r.setex(full_key, ttl, json.dumps(result, default=str))
    except Exception as e:
        logger.warning(f"Cache write failed for {key}: {e}")

    return result


async def invalidate(key: str):
    """Delete a cached key."""
    try:
        r = await redis_client.get_client()
        if r:
            await r.delete(f"{CACHE_PREFIX}{key}")
    except Exception:
        pass


async def invalidate_pattern(pattern: str):
    """Delete all keys matching a pattern."""
    try:
        r = await redis_client.get_client()
        if r:
            keys = []
            async for k in r.scan_iter(match=f"{CACHE_PREFIX}{pattern}", count=200):
                keys.append(k)
            if keys:
                await r.delete(*keys)
    except Exception:
        pass
