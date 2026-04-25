from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.player_repository import PlayerRepository
from src.database.postgres.repositories.user_repository import UserRepository
from src.database.postgres.schemas.team_schema import TeamSchema
from src.database.postgres.schemas.match_squad_schema import MatchSquadSchema


class PlayerService:

    @staticmethod
    async def create_player(
        session: AsyncSession, user_id: int, first_name: str, last_name: str = None,
        mobile: str = None, batting_style: str = None, bowling_style: str = None,
        role: str = None, profile_image: str = None, linked_user_id: int = None,
        date_of_birth=None, bio: str = None, city: str = None,
        state_province: str = None, country: str = None,
        is_guest: bool = False,
    ):
        data = {
            "first_name": first_name, "last_name": last_name,
            "full_name": f"{first_name} {last_name}" if last_name else first_name,
            "mobile": mobile if not is_guest else None,
            "is_guest": bool(is_guest),
            "date_of_birth": date_of_birth,
            "bio": bio, "city": city, "state_province": state_province, "country": country,
            "batting_style": batting_style, "bowling_style": bowling_style,
            "role": role, "profile_image": profile_image, "created_by": user_id,
        }
        # Auto-link rules:
        #   1. Explicit linked_user_id always wins.
        #   2. Guest players never auto-link (by definition).
        #   3. Otherwise, if a user exists with this mobile, attach their
        #      identity (user_id + name from user row + profile photo).
        if linked_user_id:
            data["user_id"] = linked_user_id
        elif not is_guest and mobile:
            existing_user = await UserRepository.get_by_mobile(session, mobile)
            if existing_user:
                data["user_id"] = existing_user.id
                # User's self-declared identity overrides admin-typed name.
                # Consistent with the link-on-register path in AuthService.
                data["first_name"] = existing_user.first_name or data["first_name"]
                data["last_name"] = existing_user.last_name or data["last_name"]
                data["full_name"] = existing_user.full_name or data["full_name"]
                # Only pick up user's photo if the admin didn't supply one.
                if not profile_image and getattr(existing_user, "profile", None):
                    data["profile_image"] = existing_user.profile
        return await PlayerRepository.create(session, data)

    @staticmethod
    async def get_or_create_for_user(session: AsyncSession, linked_user_id: int, created_by: int):
        """Find existing player linked to user, or create one from user profile."""
        existing = await PlayerRepository.get_by_user_id(session, linked_user_id)
        if existing:
            return existing
        user = await UserRepository.get_by_id(session, linked_user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return await PlayerRepository.create(session, {
            "first_name": user.first_name, "last_name": user.last_name,
            "full_name": user.full_name, "mobile": user.mobile,
            "user_id": linked_user_id, "created_by": created_by,
        })

    @staticmethod
    async def get_player(session: AsyncSession, player_id: int):
        player = await PlayerRepository.get_by_id(session, player_id)
        if not player:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
        return player

    @staticmethod
    async def get_players(session: AsyncSession, search: str = None, created_by: int = None, limit: int = 50, offset: int = 0):
        return await PlayerRepository.get_all(session, search=search, created_by=created_by, limit=limit, offset=offset)

    @staticmethod
    async def update_player(session: AsyncSession, player_id: int, data: dict):
        if "first_name" in data or "last_name" in data:
            player = await PlayerRepository.get_by_id(session, player_id)
            if player:
                fn = data.get("first_name", player.first_name)
                ln = data.get("last_name", player.last_name)
                data["full_name"] = f"{fn} {ln}" if ln else fn
        player = await PlayerRepository.update(session, player_id, data)
        if not player:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
        return player

    @staticmethod
    async def get_full_stats(session: AsyncSession, player_id: int, viewer_id: int | None = None) -> dict:
        """Full player profile including career stats, recent form, teams, and
        viewer-specific follow status. Cached in Redis for 30s (shared body);
        follow/self flags are attached per-caller and never cached."""
        from src.database.redis.match_cache import MatchCache

        cache_key = f"player_stats:{player_id}"
        cached = await MatchCache.get_generic(cache_key)
        if cached:
            # _attach_viewer_flags is async — without await the handler would
            # return a coroutine and FastAPI blows up trying to serialise it.
            return await PlayerService._attach_viewer_flags(session, cached, viewer_id)

        body, linked_user_id, follow_from_cte = await PlayerService._compute_stats_body(
            session, player_id, viewer_id
        )
        await MatchCache.set_generic(cache_key, body, ttl=30)
        # Attach viewer flags from the same CTE call (no extra query)
        result = dict(body)
        result["is_self"] = bool(viewer_id and linked_user_id and viewer_id == linked_user_id)
        result["is_following"] = False if result["is_self"] else bool(follow_from_cte)
        return result

    @staticmethod
    async def _attach_viewer_flags(session: AsyncSession, cached_body: dict, viewer_id: int | None) -> dict:
        """On cache hit, compute viewer-specific is_following/is_self with one small indexed query."""
        body = dict(cached_body)
        body["is_following"] = False
        body["is_self"] = False
        target_user_id = (body.get("player") or {}).get("user_id")
        if not (viewer_id and target_user_id):
            return body
        if viewer_id == target_user_id:
            body["is_self"] = True
            return body
        from src.database.postgres.schemas.user_schema import UserFollowSchema
        res = await session.execute(
            select(UserFollowSchema.follower_id).where(
                UserFollowSchema.follower_id == viewer_id,
                UserFollowSchema.following_id == target_user_id,
            )
        )
        body["is_following"] = res.scalar_one_or_none() is not None
        return body

    @staticmethod
    async def _compute_stats_body(session: AsyncSession, player_id: int, viewer_id: int | None):
        """Compute the heavy shared-body of player stats. Returns (body, linked_user_id, is_following)."""
        combined = await session.execute(
            text(_SQL_PLAYER_CORE),
            {"pid": player_id, "viewer_id": viewer_id or 0},
        )
        crow = combined.mappings().first()
        if not crow or not crow["player_json"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")

        pj = crow["player_json"]
        uj = crow["user_json"] or {}
        batj = crow["bat_json"] or {}
        bowlj = crow["bowl_json"] or {}
        bestj = crow["best_json"] or {}
        matches_played = crow["matches_played"] or 0
        is_following_flag = bool(crow["is_following"])

        batting = _build_batting(batj)
        bowling = _build_bowling(bowlj, bestj)

        teams_result = await session.execute(
            select(TeamSchema.id, TeamSchema.name, TeamSchema.short_name, TeamSchema.color)
            .join(MatchSquadSchema, MatchSquadSchema.team_id == TeamSchema.id)
            .where(MatchSquadSchema.player_id == player_id)
            .group_by(TeamSchema.id)
        )
        teams = [{"id": t.id, "name": t.name, "short_name": t.short_name, "color": t.color}
                 for t in teams_result.all()]

        recent_innings = await _fetch_recent_batting(session, player_id)
        recent_bowling = await _fetch_recent_bowling(session, player_id)

        format_stats = await _fetch_format_stats(session, player_id)

        body = {
            "player": _merge_player_user(pj, uj),
            "matches_played": matches_played,
            "teams": teams,
            "batting": batting,
            "bowling": bowling,
            "format_stats": format_stats,
            "recent_innings": recent_innings,
            "recent_bowling": recent_bowling,
        }
        return body, uj.get("u_id"), is_following_flag


def _build_batting(batj: dict) -> dict:
    innings = batj.get("innings") or 0
    runs = batj.get("runs") or 0
    balls = batj.get("balls") or 0
    outs = batj.get("outs") or 0
    hundreds = batj.get("hundreds") or 0
    return {
        "innings": innings, "runs": runs, "balls_faced": balls,
        "fours": batj.get("fours") or 0, "sixes": batj.get("sixes") or 0,
        "highest": batj.get("highest") or 0,
        "average": round(runs / outs, 2) if outs > 0 else runs,
        "strike_rate": round((runs / balls) * 100, 2) if balls > 0 else 0.0,
        "not_outs": innings - outs,
        "fifties": (batj.get("fifties_plus") or 0) - hundreds,
        "hundreds": hundreds,
    }


def _build_bowling(bowlj: dict, bestj: dict) -> dict:
    innings = bowlj.get("innings") or 0
    overs = float(bowlj.get("overs") or 0)
    runs = bowlj.get("runs") or 0
    wickets = bowlj.get("wickets") or 0
    return {
        "innings": innings, "overs": overs,
        "maidens": bowlj.get("maidens") or 0, "runs_conceded": runs,
        "wickets": wickets, "wides": bowlj.get("wides") or 0,
        "no_balls": bowlj.get("no_balls") or 0, "dot_balls": bowlj.get("dots") or 0,
        "economy": round(runs / overs, 2) if overs > 0 else 0.0,
        "average": round(runs / wickets, 2) if wickets > 0 else 0.0,
        "best": f"{bestj.get('best_w')}/{bestj.get('best_r')}" if bestj.get("best_w") else "0/0",
    }


def _merge_player_user(pj: dict, uj: dict) -> dict:
    """User-level fields override player-level ones when linked user account exists."""
    def pick(u_key: str, p_key: str):
        return uj.get(u_key) if uj.get(u_key) else pj.get(p_key)

    dob = pick("u_dob", "date_of_birth")
    return {
        "id": pj.get("id"),
        "user_id": pj.get("user_id"),
        "username": uj.get("username"),
        "first_name": pj.get("first_name"),
        "last_name": pj.get("last_name"),
        "full_name": pj.get("full_name"),
        "mobile": pj.get("mobile"),
        "date_of_birth": str(dob) if dob else None,
        "bio": pick("u_bio", "bio"),
        "city": pick("u_city", "city"),
        "state_province": pick("u_sp", "state_province"),
        "country": pick("u_country", "country"),
        "batting_style": pick("u_bs", "batting_style"),
        "bowling_style": pick("u_bws", "bowling_style"),
        "role": pick("u_role", "role"),
        "profile_image": pick("u_profile", "profile_image"),
        "email": uj.get("email"),
        "followers_count": uj.get("followers_count") or 0,
        "following_count": uj.get("following_count") or 0,
    }


async def _fetch_recent_batting(session: AsyncSession, player_id: int) -> list[dict]:
    res = await session.execute(text(_SQL_RECENT_BATTING), {"pid": player_id})
    out = []
    for r in res.mappings().all():
        result_char = None
        if r["winner_id"]:
            result_char = "W" if r["winner_id"] != r["bowling_team_id"] else "L"
        out.append({
            "match_id": r["match_id"], "match_code": r["match_code"],
            "innings_number": r["innings_number"],
            "runs": r["runs"], "balls_faced": r["balls_faced"],
            "fours": r["fours"], "sixes": r["sixes"],
            "is_out": r["is_out"], "how_out": r["how_out"],
            "match_format": f"T{r['match_overs']}" if r["match_overs"] else None,
            "match_date": str(r["match_date"]) if r["match_date"] else None,
            "opponent_team": r["opponent_team"],
            "result": result_char,
        })
    return out


async def _fetch_recent_bowling(session: AsyncSession, player_id: int) -> list[dict]:
    res = await session.execute(text(_SQL_RECENT_BOWLING), {"pid": player_id})
    return [
        {
            "match_id": r["match_id"], "match_code": r["match_code"],
            "innings_number": r["innings_number"],
            "overs": float(r["overs_bowled"] or 0), "runs": r["runs_conceded"] or 0,
            "wickets": r["wickets"] or 0, "maidens": r["maidens"] or 0,
            "economy": float(r["economy_rate"] or 0), "dots": r["dot_balls"] or 0,
            "wides": r["wides"] or 0, "no_balls": r["no_balls"] or 0,
            "match_format": f"T{r['match_overs']}" if r["match_overs"] else None,
            "match_date": str(r["match_date"]) if r["match_date"] else None,
            "opponent_team": r["opponent_team"],
        }
        for r in res.mappings().all()
    ]


async def _fetch_format_stats(session: AsyncSession, player_id: int) -> dict:
    res = await session.execute(text(_SQL_FORMAT_STATS), {"pid": player_id})
    out: dict = {}
    for row in res.mappings().all():
        overs = row["overs"] or 20
        label = f"T{overs}"
        entry = {"matches": row["bat_matches"] or 0, "batting": {}, "bowling": {}}
        if row["bat_innings"]:
            runs = row["bat_runs"] or 0
            balls = row["bat_balls"] or 0
            outs = row["bat_outs"] or 0
            entry["batting"] = {
                "innings": row["bat_innings"] or 0, "runs": runs, "balls_faced": balls,
                "fours": row["bat_fours"] or 0, "sixes": row["bat_sixes"] or 0,
                "highest": row["bat_highest"] or 0,
                "average": round(runs / outs, 2) if outs > 0 else float(runs),
                "strike_rate": round((runs / balls) * 100, 2) if balls > 0 else 0.0,
            }
        if row["bowl_innings"]:
            bo = float(row["overs_bowled"] or 0)
            br = row["runs_conceded"] or 0
            bw = row["wickets"] or 0
            entry["bowling"] = {
                "innings": row["bowl_innings"] or 0,
                "overs": bo, "wickets": bw, "runs_conceded": br,
                "maidens": row["maidens"] or 0,
                "economy": round(br / bo, 2) if bo > 0 else 0.0,
                "average": round(br / bw, 2) if bw > 0 else 0.0,
            }
        out[label] = entry
    return out


_SQL_PLAYER_CORE = """
    WITH
    p AS (
        SELECT id, user_id, first_name, last_name, full_name, mobile,
               date_of_birth, bio, city, state_province, country,
               batting_style, bowling_style, role, profile_image
        FROM players WHERE id = :pid
    ),
    u AS (
        SELECT u.id AS u_id, u.username, u.email, u.profile AS u_profile,
               u.bio AS u_bio, u.city AS u_city, u.state_province AS u_sp,
               u.country AS u_country, u.date_of_birth AS u_dob,
               u.batting_style AS u_bs, u.bowling_style AS u_bws,
               u.player_role AS u_role, u.followers_count, u.following_count
        FROM users u
        WHERE u.id = (SELECT user_id FROM p)
    ),
    bat AS (
        SELECT
            COUNT(*) AS innings,
            COALESCE(SUM(runs), 0) AS runs,
            COALESCE(SUM(balls_faced), 0) AS balls,
            COALESCE(SUM(fours), 0) AS fours,
            COALESCE(SUM(sixes), 0) AS sixes,
            COALESCE(MAX(runs), 0) AS highest,
            COALESCE(SUM(CASE WHEN is_out THEN 1 ELSE 0 END), 0) AS outs,
            COALESCE(SUM(CASE WHEN runs >= 50 THEN 1 ELSE 0 END), 0) AS fifties_plus,
            COALESCE(SUM(CASE WHEN runs >= 100 THEN 1 ELSE 0 END), 0) AS hundreds
        FROM batting_scorecards WHERE player_id = :pid
    ),
    -- Single scan over the player's bowling rows: aggregate totals + pick the
    -- 'best' spell in one pass using conditional aggregates + MIN-by-tuple.
    -- Previously this was two CTEs hitting bowling_scorecards twice — the
    -- covering index on player_id made each scan cheap, but at millions of
    -- rows halving the reads is still worthwhile.
    bowl_raw AS (
        SELECT
            COUNT(*) AS innings,
            COALESCE(SUM(overs_bowled), 0) AS overs,
            COALESCE(SUM(maidens), 0) AS maidens,
            COALESCE(SUM(runs_conceded), 0) AS runs,
            COALESCE(SUM(wickets), 0) AS wickets,
            COALESCE(SUM(wides), 0) AS wides,
            COALESCE(SUM(no_balls), 0) AS no_balls,
            COALESCE(SUM(dot_balls), 0) AS dots,
            -- ARRAY_AGG with ORDER BY + FILTER picks the best spell in the
            -- same pass as the aggregates. [1] is the top-sorted element.
            (ARRAY_AGG(wickets ORDER BY wickets DESC, runs_conceded ASC)
                FILTER (WHERE wickets > 0))[1] AS best_w,
            (ARRAY_AGG(runs_conceded ORDER BY wickets DESC, runs_conceded ASC)
                FILTER (WHERE wickets > 0))[1] AS best_r
        FROM bowling_scorecards WHERE player_id = :pid
    ),
    bowl AS (
        SELECT innings, overs, maidens, runs, wickets, wides, no_balls, dots
        FROM bowl_raw
    ),
    best AS (
        SELECT best_w, best_r FROM bowl_raw WHERE best_w IS NOT NULL
    ),
    mc AS (
        SELECT COUNT(DISTINCT match_id) AS matches
        FROM match_squads WHERE player_id = :pid
    ),
    fol AS (
        SELECT EXISTS(
            SELECT 1 FROM user_follows uf
            WHERE uf.follower_id = :viewer_id
              AND uf.following_id = (SELECT user_id FROM p)
        ) AS is_following
    )
    SELECT
        (SELECT row_to_json(p) FROM p) AS player_json,
        (SELECT row_to_json(u) FROM u) AS user_json,
        (SELECT row_to_json(bat) FROM bat) AS bat_json,
        (SELECT row_to_json(bowl) FROM bowl) AS bowl_json,
        (SELECT row_to_json(best) FROM best) AS best_json,
        (SELECT matches FROM mc) AS matches_played,
        (SELECT is_following FROM fol) AS is_following
"""

_SQL_RECENT_BATTING = """
    SELECT * FROM (
        SELECT DISTINCT ON (i.match_id)
            bs.id AS bs_id,
            bs.runs, bs.balls_faced, bs.fours, bs.sixes, bs.is_out, bs.how_out,
            i.match_id, i.innings_number, i.bowling_team_id,
            m.overs AS match_overs, m.match_date, m.winner_id, m.match_code,
            COALESCE(t.short_name, t.name) AS opponent_team
        FROM batting_scorecards bs
        JOIN innings i ON bs.innings_id = i.id
        JOIN matches m ON i.match_id = m.id
        LEFT JOIN teams t ON t.id = i.bowling_team_id
        WHERE bs.player_id = :pid
          AND m.status IN ('completed', 'live', 'in_progress')
        ORDER BY i.match_id, bs.id DESC
    ) x
    ORDER BY x.bs_id DESC
    LIMIT 10
"""

_SQL_RECENT_BOWLING = """
    SELECT * FROM (
        SELECT DISTINCT ON (i.match_id)
            bws.id AS bws_id,
            bws.overs_bowled, bws.runs_conceded, bws.wickets, bws.maidens,
            bws.economy_rate, bws.dot_balls, bws.wides, bws.no_balls,
            i.match_id, i.innings_number, i.batting_team_id,
            m.overs AS match_overs, m.match_date, m.match_code,
            COALESCE(t.short_name, t.name) AS opponent_team
        FROM bowling_scorecards bws
        JOIN innings i ON bws.innings_id = i.id
        JOIN matches m ON i.match_id = m.id
        LEFT JOIN teams t ON t.id = i.batting_team_id
        WHERE bws.player_id = :pid
          AND m.status IN ('completed', 'live', 'in_progress')
        ORDER BY i.match_id, bws.id DESC
    ) x
    ORDER BY x.bws_id DESC
    LIMIT 10
"""

_SQL_FORMAT_STATS = """
    WITH bat_by_fmt AS (
        SELECT m.overs AS overs,
            COUNT(DISTINCT ms.match_id) AS matches,
            COALESCE(SUM(bs.runs), 0) AS runs,
            COALESCE(SUM(bs.balls_faced), 0) AS balls,
            COALESCE(SUM(bs.fours), 0) AS fours,
            COALESCE(SUM(bs.sixes), 0) AS sixes,
            COALESCE(MAX(bs.runs), 0) AS highest,
            COUNT(bs.id) AS innings,
            COALESCE(SUM(CASE WHEN bs.is_out THEN 1 ELSE 0 END), 0) AS outs
        FROM batting_scorecards bs
        JOIN innings i ON bs.innings_id = i.id
        JOIN matches m ON i.match_id = m.id
        JOIN match_squads ms ON ms.match_id = m.id AND ms.player_id = :pid
        WHERE bs.player_id = :pid
        GROUP BY m.overs
    ),
    bowl_by_fmt AS (
        SELECT m.overs AS overs,
            COALESCE(SUM(bws.overs_bowled), 0) AS overs_bowled,
            COALESCE(SUM(bws.runs_conceded), 0) AS runs_conceded,
            COALESCE(SUM(bws.wickets), 0) AS wickets,
            COALESCE(SUM(bws.maidens), 0) AS maidens,
            COUNT(bws.id) AS innings
        FROM bowling_scorecards bws
        JOIN innings i ON bws.innings_id = i.id
        JOIN matches m ON i.match_id = m.id
        WHERE bws.player_id = :pid
        GROUP BY m.overs
    )
    SELECT
        COALESCE(b.overs, w.overs) AS overs,
        b.matches AS bat_matches, b.runs AS bat_runs, b.balls AS bat_balls,
        b.fours AS bat_fours, b.sixes AS bat_sixes, b.highest AS bat_highest,
        b.innings AS bat_innings, b.outs AS bat_outs,
        w.overs_bowled, w.runs_conceded, w.wickets, w.maidens,
        w.innings AS bowl_innings
    FROM bat_by_fmt b
    FULL OUTER JOIN bowl_by_fmt w ON b.overs = w.overs
"""
