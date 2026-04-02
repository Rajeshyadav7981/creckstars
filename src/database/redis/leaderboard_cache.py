"""Redis Sorted Sets for instant leaderboard rankings."""
import json
from src.database.redis.redis_client import redis_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


class LeaderboardCache:
    """Uses Redis Sorted Sets (ZADD/ZREVRANGE) for O(log N) leaderboard operations."""

    @staticmethod
    async def _get_redis():
        return await redis_client.get_client()

    # TTL for leaderboard keys: 24 hours (auto-cleanup for finished tournaments)
    LEADERBOARD_TTL = 86400

    @staticmethod
    async def update_batting_stats(tournament_id, player_id, player_name, runs, balls, fours, sixes):
        """Update a player's batting stats in the tournament leaderboard sorted set."""
        try:
            r = await LeaderboardCache._get_redis()
            # Sorted set keyed by tournament, scored by runs
            pipe = r.pipeline()
            pipe.zadd(f"lb:bat:runs:{tournament_id}", {str(player_id): runs})
            pipe.zadd(f"lb:bat:sixes:{tournament_id}", {str(player_id): sixes})
            pipe.zadd(f"lb:bat:fours:{tournament_id}", {str(player_id): fours})
            # Set TTL on leaderboard keys so finished tournaments auto-expire
            pipe.expire(f"lb:bat:runs:{tournament_id}", LeaderboardCache.LEADERBOARD_TTL)
            pipe.expire(f"lb:bat:sixes:{tournament_id}", LeaderboardCache.LEADERBOARD_TTL)
            pipe.expire(f"lb:bat:fours:{tournament_id}", LeaderboardCache.LEADERBOARD_TTL)
            # Store player metadata in hash
            pipe.hset(f"lb:player:{player_id}", mapping={
                "name": player_name,
                "runs": runs, "balls": balls, "fours": fours, "sixes": sixes,
            })
            pipe.expire(f"lb:player:{player_id}", LeaderboardCache.LEADERBOARD_TTL)
            await pipe.execute()
        except Exception as e:
            logger.warning(f"Failed to update batting stats for player {player_id} in tournament {tournament_id}: {e}")

    @staticmethod
    async def update_bowling_stats(tournament_id, player_id, player_name, wickets, runs_conceded, overs):
        """Update bowling leaderboard."""
        try:
            r = await LeaderboardCache._get_redis()
            pipe = r.pipeline()
            pipe.zadd(f"lb:bowl:wickets:{tournament_id}", {str(player_id): wickets})
            pipe.expire(f"lb:bowl:wickets:{tournament_id}", LeaderboardCache.LEADERBOARD_TTL)
            pipe.hset(f"lb:player:{player_id}", mapping={
                "name": player_name,
                "bowl_wickets": wickets, "bowl_runs": runs_conceded, "bowl_overs": str(overs),
            })
            pipe.expire(f"lb:player:{player_id}", LeaderboardCache.LEADERBOARD_TTL)
            await pipe.execute()
        except Exception as e:
            logger.warning(f"Failed to update bowling stats for player {player_id} in tournament {tournament_id}: {e}")

    @staticmethod
    async def get_top_batsmen(tournament_id, limit=10):
        """Get top batsmen by runs using ZREVRANGE -- O(log N + limit)."""
        try:
            r = await LeaderboardCache._get_redis()
            # ZREVRANGE returns highest scores first
            results = await r.zrevrange(f"lb:bat:runs:{tournament_id}", 0, limit - 1, withscores=True)
            if not results:
                return None
            # Batch-fetch all player metadata in a single pipeline
            pipe = r.pipeline()
            pids = []
            for pid_bytes, score in results:
                pid = pid_bytes if isinstance(pid_bytes, str) else pid_bytes.decode()
                pids.append((pid, score))
                pipe.hgetall(f"lb:player:{pid}")
            metas = await pipe.execute()
            players = []
            for (pid, score), meta in zip(pids, metas):
                if meta:
                    name = meta.get("name", meta.get(b"name", b""))
                    if isinstance(name, bytes):
                        name = name.decode()
                    players.append({
                        "player_id": int(pid),
                        "name": name,
                        "runs": int(score),
                        "balls": int(meta.get("balls", meta.get(b"balls", 0))),
                        "fours": int(meta.get("fours", meta.get(b"fours", 0))),
                        "sixes": int(meta.get("sixes", meta.get(b"sixes", 0))),
                    })
            return players if players else None
        except Exception as e:
            logger.warning(f"Failed to get top batsmen for tournament {tournament_id}: {e}")
            return None

    @staticmethod
    async def get_top_bowlers(tournament_id, limit=10):
        """Get top bowlers by wickets using ZREVRANGE."""
        try:
            r = await LeaderboardCache._get_redis()
            results = await r.zrevrange(f"lb:bowl:wickets:{tournament_id}", 0, limit - 1, withscores=True)
            if not results:
                return None
            # Batch-fetch all player metadata in a single pipeline
            pipe = r.pipeline()
            pids = []
            for pid_bytes, score in results:
                pid = pid_bytes if isinstance(pid_bytes, str) else pid_bytes.decode()
                pids.append((pid, score))
                pipe.hgetall(f"lb:player:{pid}")
            metas = await pipe.execute()
            players = []
            for (pid, score), meta in zip(pids, metas):
                if meta:
                    name = meta.get("name", meta.get(b"name", b""))
                    if isinstance(name, bytes):
                        name = name.decode()
                    players.append({
                        "player_id": int(pid),
                        "name": name,
                        "wickets": int(score),
                        "runs_conceded": int(meta.get("bowl_runs", meta.get(b"bowl_runs", 0))),
                        "overs": meta.get("bowl_overs", meta.get(b"bowl_overs", "0")),
                    })
            return players if players else None
        except Exception as e:
            logger.warning(f"Failed to get top bowlers for tournament {tournament_id}: {e}")
            return None

    @staticmethod
    async def get_player_rank(tournament_id, player_id, stat="runs"):
        """Get a player's rank in a leaderboard -- O(log N)."""
        try:
            r = await LeaderboardCache._get_redis()
            key = f"lb:bat:{stat}:{tournament_id}" if stat in ("runs", "fours", "sixes") else f"lb:bowl:{stat}:{tournament_id}"
            rank = await r.zrevrank(key, str(player_id))
            return rank + 1 if rank is not None else None
        except Exception as e:
            logger.warning(f"Failed to get player rank for player {player_id} in tournament {tournament_id}: {e}")
            return None

    @staticmethod
    async def invalidate_tournament(tournament_id):
        """Clear all leaderboard data for a tournament."""
        try:
            r = await LeaderboardCache._get_redis()
            keys = await r.keys(f"lb:*:{tournament_id}")
            if keys:
                await r.delete(*keys)
        except Exception as e:
            logger.warning(f"Failed to invalidate leaderboard cache for tournament {tournament_id}: {e}")
