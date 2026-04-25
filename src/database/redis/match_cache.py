import json
from src.database.redis.redis_client import redis_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MatchCache:

    @staticmethod
    async def _get_redis():
        return await redis_client.get_client()

    @staticmethod
    async def get_live_state(match_id: int):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:state"
            data = await r.get(key)
            return json.loads(data) if data else None
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
            await r.set(key, json.dumps(data, default=str), ex=ttl)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    @staticmethod
    async def get_scorecard(match_id: int):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:scorecard"
            data = await r.get(key)
            return json.loads(data) if data else None
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
            await r.set(key, json.dumps(data, default=str), ex=ttl)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    @staticmethod
    async def set_current_over(match_id: int, balls: list):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:current_over"
            await r.delete(key)
            if balls:
                await r.rpush(key, *balls)
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
            return json.loads(data) if data else None
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")
            return None

    @staticmethod
    async def set_squad(match_id: int, team_id: int, data: list, ttl: int = 300):
        try:
            r = await MatchCache._get_redis()
            key = f"match:{match_id}:squad:{team_id}"
            await r.set(key, json.dumps(data, default=str), ex=ttl)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    # --- Pub/Sub ---

    @staticmethod
    async def publish_update(match_id: int, event_type: str, data: dict):
        try:
            r = await MatchCache._get_redis()
            channel = f"match:{match_id}:live"
            message = json.dumps({"type": event_type, "data": data})
            await r.publish(channel, message)
        except Exception as e:
            logger.warning(f"Redis operation failed: {e}")

    # --- Generic cache (for commentary, etc.) ---

    @staticmethod
    async def get_generic(key: str):
        try:
            r = await MatchCache._get_redis()
            data = await r.get(f"cache:{key}")
            return json.loads(data) if data else None
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
            await r.set(f"cache:{key}", json.dumps(data, default=str), ex=ttl)
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
