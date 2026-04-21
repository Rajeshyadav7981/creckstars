import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.redis.redis_client import redis_client
from src.utils.logger import get_logger

logger = get_logger(__name__)

STATS_CACHE_TTL = 60  # seconds — invalidated on writes


class UserStatsService:
    """Activity stats for /api/users/me/stats — organized/played counts,
    deduplicated totals, all in one SQL round-trip. Redis-cached per-user."""

    @staticmethod
    async def _get_redis():
        try:
            return await redis_client.get_client()
        except Exception:
            return None

    @staticmethod
    async def invalidate(user_id: int):
        """Clear the cache for a user. Call on create/delete of matches, teams,
        tournaments, or squad/team-membership changes affecting this user."""
        r = await UserStatsService._get_redis()
        if not r:
            return
        try:
            await r.delete(f"stats:{user_id}")
        except Exception as e:
            logger.warning(f"Failed to invalidate stats cache for user {user_id}: {e}")

    @staticmethod
    async def get(session: AsyncSession, user_id: int) -> tuple[dict, bool]:
        """Fetch stats for a user. Returns (data, was_cached)."""
        cache_key = f"stats:{user_id}"
        r = await UserStatsService._get_redis()
        if r:
            try:
                cached = await r.get(cache_key)
                if cached:
                    return json.loads(cached), True
            except Exception as _e:
                pass  # logged below not to crash hot path

        data = await UserStatsService._compute(session, user_id)

        if r:
            try:
                await r.setex(cache_key, STATS_CACHE_TTL, json.dumps(data, default=str))
            except Exception as _e:
                pass  # logged below not to crash hot path

        return data, False

    @staticmethod
    async def _compute(session: AsyncSession, uid: int) -> dict:
        """Single-query stats computation via CTEs — one DB round-trip."""
        result = await session.execute(text(_SQL_USER_STATS), {"uid": uid})
        row = result.mappings().first()
        return {
            "created": {
                "teams": row["teams_created"],
                "matches": row["matches_created"],
                "matches_completed": row["matches_created_completed"],
                "matches_live": row["matches_created_live"],
                "matches_upcoming": row["matches_created_upcoming"],
                "tournaments": row["tournaments_created"],
                "tournaments_completed": row["tournaments_created_completed"],
                "tournaments_active": row["tournaments_created_active"],
                "players": row["players_created"],
            },
            "played": {
                "matches": row["matches_played"],
                "matches_completed": row["matches_played_completed"],
                "matches_live": row["matches_played_live"],
                "tournaments": row["tournaments_played"],
                "teams": row["teams_member"],
            },
            "total": {
                "matches": row["total_matches"],
                "completed": row["total_completed"],
                "teams": row["total_teams"],
                "tournaments": row["total_tournaments"],
            },
        }


# ── SQL (compiled once at module load) ──────────────────────────────────────

_SQL_USER_STATS = """
    WITH
    my_players AS (
        SELECT id FROM players WHERE user_id = :uid
    ),
    played_matches AS (
        SELECT DISTINCT ms.match_id, mm.status, mm.tournament_id
        FROM match_squads ms
        JOIN matches mm ON mm.id = ms.match_id
        WHERE ms.player_id IN (TABLE my_players)
          AND mm.status IN ('live', 'in_progress', 'completed')
    ),
    created_matches AS (
        SELECT
            COUNT(*) AS matches,
            COUNT(*) FILTER (WHERE status = 'completed') AS matches_completed,
            COUNT(*) FILTER (WHERE status IN ('live', 'in_progress')) AS matches_live,
            COUNT(*) FILTER (WHERE status IN ('upcoming', 'scheduled', 'created', 'toss', 'squad_set')) AS matches_upcoming
        FROM matches WHERE created_by = :uid
    ),
    created_tourn AS (
        SELECT
            COUNT(*) AS tournaments,
            COUNT(*) FILTER (WHERE status = 'completed') AS tournaments_completed,
            COUNT(*) FILTER (WHERE status = 'in_progress') AS tournaments_active
        FROM tournaments WHERE created_by = :uid
    ),
    played_agg AS (
        SELECT
            COUNT(*) AS matches,
            COUNT(*) FILTER (WHERE status = 'completed') AS matches_completed,
            COUNT(*) FILTER (WHERE status IN ('live', 'in_progress')) AS matches_live
        FROM played_matches
    ),
    played_tournaments AS (
        SELECT COUNT(DISTINCT tt.tournament_id) AS tournaments
        FROM tournament_teams tt
        JOIN team_players tp ON tp.team_id = tt.team_id
        WHERE tp.player_id IN (TABLE my_players)
    ),
    played_teams AS (
        SELECT COUNT(DISTINCT team_id) AS teams
        FROM team_players WHERE player_id IN (TABLE my_players)
    ),
    created_match_ids AS (SELECT id FROM matches WHERE created_by = :uid),
    created_team_ids AS (SELECT id FROM teams WHERE created_by = :uid),
    created_tourn_ids AS (SELECT id FROM tournaments WHERE created_by = :uid),
    played_tourn_ids AS (
        SELECT DISTINCT tt.tournament_id AS id
        FROM tournament_teams tt
        JOIN team_players tp ON tp.team_id = tt.team_id
        WHERE tp.player_id IN (TABLE my_players)
    ),
    member_team_ids AS (
        SELECT DISTINCT team_id AS id FROM team_players WHERE player_id IN (TABLE my_players)
    ),
    totals AS (
        SELECT
            (SELECT COUNT(*) FROM created_match_ids) +
              (SELECT COUNT(*) FROM played_matches WHERE match_id NOT IN (SELECT id FROM created_match_ids)) AS matches,
            (SELECT COUNT(*) FROM matches WHERE created_by = :uid AND status = 'completed') +
              (SELECT COUNT(*) FROM played_matches WHERE status = 'completed' AND match_id NOT IN (SELECT id FROM created_match_ids)) AS completed,
            (SELECT COUNT(*) FROM created_team_ids) +
              (SELECT COUNT(*) FROM member_team_ids WHERE id NOT IN (SELECT id FROM created_team_ids)) AS teams,
            (SELECT COUNT(*) FROM created_tourn_ids) +
              (SELECT COUNT(*) FROM played_tourn_ids WHERE id NOT IN (SELECT id FROM created_tourn_ids)) AS tournaments
    ),
    player_count AS (
        SELECT COUNT(*) AS n FROM players WHERE created_by = :uid
    )
    SELECT
        cm.matches AS matches_created,
        cm.matches_completed AS matches_created_completed,
        cm.matches_live AS matches_created_live,
        cm.matches_upcoming AS matches_created_upcoming,
        (SELECT COUNT(*) FROM created_team_ids) AS teams_created,
        ct.tournaments AS tournaments_created,
        ct.tournaments_completed AS tournaments_created_completed,
        ct.tournaments_active AS tournaments_created_active,
        pc.n AS players_created,
        pa.matches AS matches_played,
        pa.matches_completed AS matches_played_completed,
        pa.matches_live AS matches_played_live,
        ptourn.tournaments AS tournaments_played,
        pt.teams AS teams_member,
        t.matches AS total_matches,
        t.completed AS total_completed,
        t.teams AS total_teams,
        t.tournaments AS total_tournaments
    FROM created_matches cm, created_tourn ct, played_agg pa,
         played_tournaments ptourn, played_teams pt, player_count pc, totals t
"""
