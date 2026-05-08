import json

try:
    import orjson  # type: ignore

    def _dumps(obj) -> bytes:
        # orjson is ~3-5x faster than stdlib json on the live-state hot path
        # (1000+ viewers polling per match). Returns bytes — Redis accepts that.
        # default=str preserves the previous datetime-coerce behavior.
        return orjson.dumps(obj, default=str)

    def _loads(data):
        # orjson.loads accepts both str and bytes from the Redis client.
        return orjson.loads(data)
except ImportError:  # pragma: no cover — fallback if orjson is unavailable
    def _dumps(obj):  # type: ignore[no-redef]
        return json.dumps(obj, default=str)

    def _loads(data):  # type: ignore[no-redef]
        return json.loads(data)

from src.database.redis.redis_client import redis_client
from src.utils.logger import get_logger

logger = get_logger(__name__)

# How long a single-flight refresh lock survives. Long enough to outlast a slow
# DB query, short enough that a crashed worker can't wedge the cache.
_REFRESH_LOCK_TTL = 10


class MatchCache:

    @staticmethod
    async def _get_redis():
        return await redis_client.get_client()

    @staticmethod
    async def try_acquire_refresh_lock(name: str, ttl: int = _REFRESH_LOCK_TTL) -> bool:
        """Best-effort distributed single-flight. Returns True for the first caller
        to acquire the lock; subsequent callers get False until TTL elapses or the
        owner releases. Falls open (returns True) on Redis errors so we never
        deadlock when Redis is down.
        """
        try:
            r = await MatchCache._get_redis()
            return bool(await r.set(f"lock:{name}", "1", nx=True, ex=ttl))
        except Exception as e:
            logger.warning(f"Redis refresh-lock acquire failed: {e}")
            return True

    @staticmethod
    async def release_refresh_lock(name: str) -> None:
        try:
            r = await MatchCache._get_redis()
            await r.delete(f"lock:{name}")
        except Exception as e:
            logger.warning(f"Redis refresh-lock release failed: {e}")

    @staticmethod
    async def get_live_state(match_id: int):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:state"
            data = await r.get(key)
            return _loads(data) if data else None
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")
            return None

    @staticmethod
    async def set_live_state(match_id: int, data: dict, ttl: int = 5):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:state"
            if data is None:
                await r.delete(key)
                return
            await r.set(key, _dumps(data), ex=ttl)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    @staticmethod
    async def get_scorecard(match_id: int):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:scorecard"
            data = await r.get(key)
            return _loads(data) if data else None
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")
            return None

    @staticmethod
    async def set_scorecard(match_id: int, data: dict, ttl: int = 60):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:scorecard"
            if data is None:
                # Treat None as an explicit invalidation rather than caching "null"
                await r.delete(key)
                return
            await r.set(key, _dumps(data), ex=ttl)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    @staticmethod
    async def set_current_over(match_id: int, balls: list, ttl: int = 600):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:current_over"
            await r.delete(key)
            if balls:
                await r.rpush(key, *balls)
                # Bound memory: an abandoned/crashed match shouldn't leak this list forever.
                await r.expire(key, ttl)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    @staticmethod
    async def get_current_over(match_id: int):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:current_over"
            return await r.lrange(key, 0, -1)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")
            return []

    @staticmethod
    async def set_broadcast_message(match_id: int, message: str, ttl: int = 600):
        """Store an admin broadcast message for a match (default 10 min TTL)."""
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:broadcast"
            await r.set(key, message, ex=ttl)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    @staticmethod
    async def get_broadcast_message(match_id: int):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:broadcast"
            return await r.get(key)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")
            return None

    @staticmethod
    async def clear_broadcast_message(match_id: int):
        try:
            r = await MatchCache._get_redis()
            await r.delete(f"match:{match_id}:broadcast")
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    @staticmethod
    async def get_squad(match_id: int, team_id: int):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:squad:{team_id}"
            data = await r.get(key)
            return _loads(data) if data else None
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")
            return None

    @staticmethod
    async def set_squad(match_id: int, team_id: int, data: list, ttl: int = 300):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:squad:{team_id}"
            await r.set(key, _dumps(data), ex=ttl)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    # --- Pub/Sub ---

    @staticmethod
    async def publish_update(match_id: int, event_type: str, data: dict):
        try:
            r = await MatchCache._get_redis()
            channel = f"match:{match_id}:live"
            message = _dumps({"type": event_type, "data": data})
            await r.publish(channel, message)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    # --- Generic cache (for commentary, etc.) ---

    @staticmethod
    async def get_generic(key: str):
        try:
            r = await MatchCache._get_redis()
            data = await r.get(f"cache:{key}")
            return _loads(data) if data else None
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")
            return None

    @staticmethod
    async def set_generic(key: str, data, ttl: int = 30):
        try:
            r = await MatchCache._get_redis()
            if data is None:
                await r.delete(f"cache:{key}")
                return
            await r.set(f"cache:{key}", _dumps(data), ex=ttl)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    # --- Invalidation ---

    @staticmethod
    async def invalidate_match(match_id: int):
        """Delete all cached data for a match."""
        try:
            r = await MatchCache._get_redis()
            keys = [
                f"match:{match_id}:state",
                f"match:{match_id}:scorecard",
                f"match:{match_id}:current_over",
                f"cache:match_detail:{match_id}",
            ]
            # Also invalidate squad cache
            squad_keys = await r.keys(f"match:{match_id}:squad:*")
            if squad_keys:
                keys.extend(squad_keys)
            # Also invalidate commentary cache
            comm_keys = await r.keys(f"cache:comm:{match_id}:*")
            if comm_keys:
                keys.extend(comm_keys)
            await r.delete(*keys)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    # Legacy alias
    @staticmethod
    async def clear_match(match_id: int):
        await MatchCache.invalidate_match(match_id)
