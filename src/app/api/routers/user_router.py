from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import text, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user, get_current_user_optional
from src.database.postgres.repositories.user_repository import UserRepository
from src.database.postgres.schemas.user_schema import UserSchema, UserFollowSchema
from src.app.api.routers.models.user_model import UserSearchResponse
from src.utils.text_parser import validate_username
import json as _json

router = APIRouter(prefix="/api/users", tags=["Users"])


async def _get_redis():
    """Get Redis client, returns None on failure."""
    try:
        from src.database.redis.redis_client import redis_client
        return await redis_client.get_client()
    except Exception:
        return None


class SetUsernameRequest(BaseModel):
    username: str


@router.get("/search")
async def search_users(
    q: str = Query("", min_length=0),
    limit: int = Query(10, ge=1, le=20),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Search users by name or @username. Cached in Redis (60s TTL)."""
    query = q.strip().lower()
    cache_key = f"usearch:{query}:{limit}"

    # Try Redis cache first
    r = await _get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    users = await UserRepository.search(session, query, limit=limit)
    result = [
        {
            "id": u.id, "full_name": u.full_name, "first_name": u.first_name,
            "username": getattr(u, 'username', None), "profile": u.profile,
        }
        for u in users
    ]

    # Cache for 60 seconds
    if r:
        try:
            await r.setex(cache_key, 60, _json.dumps(result))
        except Exception:
            pass

    return result


@router.get("/mention-search")
async def mention_search(
    q: str = Query("", min_length=0, max_length=30),
    limit: int = Query(10, ge=1, le=15),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Fast @mention autocomplete — optimized for real-time typing.
    Uses Redis cache (30s TTL) for repeated prefix queries.
    Searches by username prefix first (exact match priority), then full_name.
    """
    q = q.strip().lower()
    cache_key = f"mention:{q}:{limit}"

    # Try Redis cache first (hot path — same prefix searched by many users)
    r = await _get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    # Query: username prefix match first (most relevant), then name match
    from sqlalchemy.orm import load_only
    if q:
        # Priority 1: username starts with query (exact prefix)
        # Priority 2: username contains query
        # Priority 3: full_name contains query
        result = await session.execute(
            select(UserSchema)
            .options(load_only(UserSchema.id, UserSchema.username, UserSchema.full_name, UserSchema.first_name, UserSchema.last_name, UserSchema.profile))
            .where(
                or_(
                    UserSchema.username.ilike(f"{q}%"),      # Prefix match (fastest, uses index)
                    UserSchema.username.ilike(f"%{q}%"),     # Contains
                    UserSchema.full_name.ilike(f"%{q}%"),    # Name search
                )
            )
            .order_by(
                # Sort: exact prefix first, then contains, then name
                (UserSchema.username.ilike(f"{q}%")).desc(),
                UserSchema.full_name,
            )
            .limit(limit)
        )
    else:
        # Empty query — show followed users first (like Instagram), then recent
        result = await session.execute(
            select(UserSchema)
            .options(load_only(UserSchema.id, UserSchema.username, UserSchema.full_name, UserSchema.first_name, UserSchema.last_name, UserSchema.profile))
            .outerjoin(UserFollowSchema, (UserFollowSchema.following_id == UserSchema.id) & (UserFollowSchema.follower_id == user.id))
            .where(UserSchema.id != user.id)
            .order_by(UserFollowSchema.created_at.desc().nullslast(), UserSchema.id.desc())
            .limit(limit)
        )

    users = [
        {
            "id": u.id,
            "username": u.username,
            "full_name": u.full_name,
            "first_name": u.first_name,
            "profile": u.profile,
        }
        for u in result.scalars().all()
    ]

    # Cache for 30 seconds
    try:
        if r:
            await r.setex(cache_key, 30, _json.dumps(users))
    except Exception:
        pass

    return users


@router.get("/@{username}")
async def get_public_profile(
    username: str,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Get a user's public profile by @username. Profile data cached 5min in Redis."""
    uname = username.lower()
    cache_key = f"profile:{uname}"
    r = await _get_redis()

    # Try profile cache (static data — follow status checked separately)
    profile_data = None
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                profile_data = _json.loads(cached)
        except Exception:
            pass

    if not profile_data:
        result = await session.execute(
            select(UserSchema).where(UserSchema.username == uname)
        )
        target = result.scalar_one_or_none()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        profile_data = {
            "id": target.id,
            "username": target.username,
            "full_name": target.full_name,
            "first_name": target.first_name,
            "last_name": target.last_name,
            "profile": target.profile,
            "followers_count": getattr(target, 'followers_count', 0) or 0,
            "following_count": getattr(target, 'following_count', 0) or 0,
        }
        # Cache profile for 5 minutes
        if r:
            try:
                await r.setex(cache_key, 300, _json.dumps(profile_data))
            except Exception:
                pass

    # Follow status is user-specific — always check live (skip for guests)
    is_following = False
    target_id = profile_data["id"]
    if user and user.id != target_id:
        follow_check = await session.execute(
            select(UserFollowSchema.follower_id).where(
                UserFollowSchema.follower_id == user.id,
                UserFollowSchema.following_id == target_id,
            )
        )
        is_following = follow_check.scalar_one_or_none() is not None

    return {
        **profile_data,
        "is_following": is_following,
        "is_self": bool(user and user.id == target_id),
    }


@router.post("/username")
async def set_username(
    req: SetUsernameRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Set or change username (@handle)."""
    username = req.username.lower().strip()
    valid, error = validate_username(username)
    if not valid:
        raise HTTPException(status_code=400, detail=error)

    # Check availability
    existing = await session.execute(
        select(UserSchema.id).where(UserSchema.username == username, UserSchema.id != user.id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already taken")

    await session.execute(
        text("UPDATE users SET username = :u WHERE id = :uid"),
        {"u": username, "uid": user.id},
    )
    await session.commit()

    # Invalidate user cache
    try:
        from src.database.redis.redis_client import redis_client
        r = await redis_client.get_client()
        if r:
            await r.delete(f"user:{user.id}")
    except Exception:
        pass

    return {"username": username}


@router.get("/username/check")
async def check_username(
    username: str = Query(..., min_length=3, max_length=30),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Check if a username is available."""
    username = username.lower().strip()
    valid, error = validate_username(username)
    if not valid:
        return {"available": False, "reason": error}

    existing = await session.execute(
        select(UserSchema.id).where(UserSchema.username == username, UserSchema.id != user.id)
    )
    available = existing.scalar_one_or_none() is None
    return {"available": available, "username": username}


@router.get("/me/stats")
async def get_my_stats(
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Get profile + activity stats for the current user.

    Differentiates between:
      - "created" = things the user organized/created
      - "played"  = matches/tournaments where the user was in a squad as a player
    """
    result = await session.execute(text("""
        WITH
        -- Player IDs linked to this user (typically 1, but could be multiple)
        my_players AS (
            SELECT id FROM players WHERE user_id = :uid
        ),
        -- Matches user played in (via squad)
        played_matches AS (
            SELECT DISTINCT ms.match_id
            FROM match_squads ms
            WHERE ms.player_id IN (SELECT id FROM my_players)
        ),
        -- Created counts (single scan of each table with conditional aggregation)
        created AS (
            SELECT
                (SELECT COUNT(*) FROM teams WHERE created_by = :uid) AS teams,
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
        -- Played counts (single scan via played_matches CTE)
        played AS (
            SELECT
                COUNT(*) AS matches,
                COUNT(*) FILTER (WHERE m.status = 'completed') AS matches_completed,
                COUNT(*) FILTER (WHERE m.status IN ('live', 'in_progress')) AS matches_live,
                COUNT(DISTINCT m.tournament_id) FILTER (WHERE m.tournament_id IS NOT NULL) AS tournaments
            FROM played_matches pm JOIN matches m ON m.id = pm.match_id
        ),
        played_teams AS (
            SELECT COUNT(DISTINCT tp.team_id) AS teams
            FROM team_players tp WHERE tp.player_id IN (SELECT id FROM my_players)
        ),
        -- Deduplicated totals (UNION removes duplicates)
        totals AS (
            SELECT
                (SELECT COUNT(*) FROM (
                    SELECT id FROM matches WHERE created_by = :uid
                    UNION SELECT match_id FROM played_matches
                ) x) AS matches,
                (SELECT COUNT(*) FROM (
                    SELECT id FROM matches WHERE created_by = :uid AND status = 'completed'
                    UNION SELECT pm.match_id FROM played_matches pm JOIN matches m ON m.id = pm.match_id WHERE m.status = 'completed'
                ) x) AS completed,
                (SELECT COUNT(*) FROM (
                    SELECT id FROM teams WHERE created_by = :uid
                    UNION SELECT tp.team_id FROM team_players tp WHERE tp.player_id IN (SELECT id FROM my_players)
                ) x) AS teams,
                (SELECT COUNT(*) FROM (
                    SELECT id FROM tournaments WHERE created_by = :uid
                    UNION SELECT DISTINCT m.tournament_id FROM played_matches pm JOIN matches m ON m.id = pm.match_id WHERE m.tournament_id IS NOT NULL
                ) x) AS tournaments
        )
        SELECT
            c.teams AS teams_created, c.matches AS matches_created,
            c.matches_completed AS matches_created_completed, c.matches_live AS matches_created_live,
            c.matches_upcoming AS matches_created_upcoming,
            ct.tournaments AS tournaments_created, ct.tournaments_completed AS tournaments_created_completed,
            ct.tournaments_active AS tournaments_created_active,
            (SELECT COUNT(*) FROM players WHERE created_by = :uid) AS players_created,
            p.matches AS matches_played, p.matches_completed AS matches_played_completed,
            p.matches_live AS matches_played_live, p.tournaments AS tournaments_played,
            pt.teams AS teams_member,
            t.matches AS total_matches, t.completed AS total_completed,
            t.teams AS total_teams, t.tournaments AS total_tournaments
        FROM created c, created_tourn ct, played p, played_teams pt, totals t
    """), {"uid": user.id})
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


# ═══════════════════════════════════════
# Follow System
# ═══════════════════════════════════════

@router.post("/follow/{target_id}")
async def follow_user(
    target_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Follow a user."""
    if target_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")

    # Check target exists
    target = await session.execute(select(UserSchema.id).where(UserSchema.id == target_id))
    if not target.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    # Check not already following
    existing = await session.execute(
        select(UserFollowSchema).where(
            UserFollowSchema.follower_id == user.id,
            UserFollowSchema.following_id == target_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"status": "already_following"}

    session.add(UserFollowSchema(follower_id=user.id, following_id=target_id))

    # Update counts
    await session.execute(text("UPDATE users SET following_count = following_count + 1 WHERE id = :uid"), {"uid": user.id})
    await session.execute(text("UPDATE users SET followers_count = followers_count + 1 WHERE id = :uid"), {"uid": target_id})
    await session.commit()

    # Invalidate Redis caches for both users
    r = await _get_redis()
    if r:
        try:
            # Get both usernames for profile cache invalidation
            t = await session.execute(select(UserSchema.username).where(UserSchema.id == target_id))
            target_username = t.scalar_one_or_none()
            keys = [f"user:{user.id}", f"user:{target_id}",
                    f"followers:{target_id}", f"following:{user.id}"]
            if user.username:
                keys.append(f"profile:{user.username.lower()}")
            if target_username:
                keys.append(f"profile:{target_username.lower()}")
            await r.delete(*keys)
        except Exception:
            pass

    return {"status": "followed"}


@router.delete("/follow/{target_id}")
async def unfollow_user(
    target_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Unfollow a user."""
    existing = await session.execute(
        select(UserFollowSchema).where(
            UserFollowSchema.follower_id == user.id,
            UserFollowSchema.following_id == target_id,
        )
    )
    follow = existing.scalar_one_or_none()
    if not follow:
        return {"status": "not_following"}

    await session.delete(follow)
    await session.execute(text("UPDATE users SET following_count = GREATEST(0, following_count - 1) WHERE id = :uid"), {"uid": user.id})
    await session.execute(text("UPDATE users SET followers_count = GREATEST(0, followers_count - 1) WHERE id = :uid"), {"uid": target_id})
    await session.commit()

    # Invalidate Redis caches for both users
    r = await _get_redis()
    if r:
        try:
            t = await session.execute(select(UserSchema.username).where(UserSchema.id == target_id))
            target_username = t.scalar_one_or_none()
            keys = [f"user:{user.id}", f"user:{target_id}",
                    f"followers:{target_id}", f"following:{user.id}"]
            if user.username:
                keys.append(f"profile:{user.username.lower()}")
            if target_username:
                keys.append(f"profile:{target_username.lower()}")
            await r.delete(*keys)
        except Exception:
            pass

    return {"status": "unfollowed"}


@router.get("/follow/{target_id}/status")
async def follow_status(
    target_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Check if current user follows target."""
    existing = await session.execute(
        select(UserFollowSchema.follower_id).where(
            UserFollowSchema.follower_id == user.id,
            UserFollowSchema.following_id == target_id,
        )
    )
    return {"is_following": existing.scalar_one_or_none() is not None}


@router.get("/{target_id}/followers")
async def get_followers(
    target_id: int,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Get followers of a user. Base list cached 120s in Redis."""
    from sqlalchemy.orm import load_only
    cache_key = f"followers:{target_id}:{offset}:{limit}"
    r = await _get_redis()
    users_data = None

    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                users_data = _json.loads(cached)
        except Exception:
            pass

    if users_data is None:
        result = await session.execute(
            select(UserSchema)
            .options(load_only(UserSchema.id, UserSchema.username, UserSchema.full_name, UserSchema.profile))
            .join(UserFollowSchema, UserSchema.id == UserFollowSchema.follower_id)
            .where(UserFollowSchema.following_id == target_id)
            .order_by(UserFollowSchema.created_at.desc())
            .limit(limit).offset(offset)
        )
        users = result.scalars().all()
        users_data = [{"id": u.id, "username": u.username, "full_name": u.full_name, "profile": u.profile} for u in users]
        if r:
            try:
                await r.setex(cache_key, 120, _json.dumps(users_data))
            except Exception:
                pass

    # Follow-back status is user-specific — always check live (skip for guests)
    user_ids = [u["id"] for u in users_data]
    following_set = set()
    if user and user_ids:
        follows_result = await session.execute(
            select(UserFollowSchema.following_id).where(
                UserFollowSchema.follower_id == user.id,
                UserFollowSchema.following_id.in_(user_ids),
            )
        )
        following_set = {row[0] for row in follows_result.all()}

    return [{**u, "is_following": u["id"] in following_set} for u in users_data]


@router.get("/{target_id}/following")
async def get_following(
    target_id: int,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Get users that target follows. Base list cached 120s in Redis."""
    from sqlalchemy.orm import load_only
    cache_key = f"following:{target_id}:{offset}:{limit}"
    r = await _get_redis()
    users_data = None

    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                users_data = _json.loads(cached)
        except Exception:
            pass

    if users_data is None:
        result = await session.execute(
            select(UserSchema)
            .options(load_only(UserSchema.id, UserSchema.username, UserSchema.full_name, UserSchema.profile))
            .join(UserFollowSchema, UserSchema.id == UserFollowSchema.following_id)
            .where(UserFollowSchema.follower_id == target_id)
            .order_by(UserFollowSchema.created_at.desc())
            .limit(limit).offset(offset)
        )
        users = result.scalars().all()
        users_data = [{"id": u.id, "username": u.username, "full_name": u.full_name, "profile": u.profile} for u in users]
        if r:
            try:
                await r.setex(cache_key, 120, _json.dumps(users_data))
            except Exception:
                pass

    # Follow-back status is user-specific — always check live (skip for guests)
    user_ids = [u["id"] for u in users_data]
    following_set = set()
    if user and user_ids:
        follows_result = await session.execute(
            select(UserFollowSchema.following_id).where(
                UserFollowSchema.follower_id == user.id,
                UserFollowSchema.following_id.in_(user_ids),
            )
        )
        following_set = {row[0] for row in follows_result.all()}

    return [{**u, "is_following": u["id"] in following_set} for u in users_data]
