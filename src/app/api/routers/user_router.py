from fastapi import APIRouter, Depends, Query, HTTPException, Response
from starlette.requests import Request
from pydantic import BaseModel
from sqlalchemy import text, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user, get_current_user_optional
from src.database.postgres.repositories.user_repository import UserRepository
from src.database.postgres.schemas.user_schema import UserSchema, UserFollowSchema
from src.utils.text_parser import validate_username
from src.utils.logger import get_logger
from src.app.api.rate_limiter import limiter
from src.app.api.config import RATE_LIMITS
import json as _json

logger = get_logger(__name__)

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
    # v2 cache key — response shape now includes player_id
    cache_key = f"usearch:v2:{query}:{limit}"

    r = await _get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception as _e:
            logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    # search() LEFT JOINs players so player_id rides back on the same row — no N+1.
    rows = await UserRepository.search(session, query, limit=limit)
    result = [
        {
            "id": u.id, "full_name": u.full_name, "first_name": u.first_name,
            "username": getattr(u, 'username', None), "profile": u.profile,
            "player_id": pid,
        }
        for u, pid in rows
    ]

    if r:
        try:
            await r.setex(cache_key, 60, _json.dumps(result))
        except Exception as _e:
            logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

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

    # Hot path: same prefix hammered by many users as they type, so cache aggressively.
    r = await _get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception as _e:
            logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    from sqlalchemy.orm import load_only
    if q:
        result = await session.execute(
            select(UserSchema)
            .options(load_only(UserSchema.id, UserSchema.username, UserSchema.full_name, UserSchema.first_name, UserSchema.last_name, UserSchema.profile))
            .where(
                or_(
                    UserSchema.username.ilike(f"{q}%"),
                    UserSchema.username.ilike(f"%{q}%"),
                    UserSchema.full_name.ilike(f"%{q}%"),
                )
            )
            .order_by(
                (UserSchema.username.ilike(f"{q}%")).desc(),
                UserSchema.full_name,
            )
            .limit(limit)
        )
    else:
        # Empty query → Instagram-style: followed users first, then recent.
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

    try:
        if r:
            await r.setex(cache_key, 30, _json.dumps(users))
    except Exception as _e:
        logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

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

    # Profile body is cacheable; follow-status is per-viewer and checked live below.
    profile_data = None
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                profile_data = _json.loads(cached)
        except Exception as _e:
            logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    if not profile_data:
        result = await session.execute(
            select(UserSchema).where(UserSchema.username == uname)
        )
        target = result.scalar_one_or_none()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        from src.database.postgres.schemas.player_schema import PlayerSchema as _PS
        pres = await session.execute(
            select(_PS.id).where(_PS.user_id == target.id).order_by(_PS.id).limit(1)
        )
        linked_player_id = pres.scalar_one_or_none()
        # Legacy backfill for users who registered before auto-create-on-register
        # existed — mint a player row on first profile view so PlayerProfile is reachable.
        if linked_player_id is None:
            try:
                from src.database.postgres.repositories.player_repository import PlayerRepository as _PR
                new_player = await _PR.create(session, {
                    "user_id": target.id,
                    "first_name": target.first_name,
                    "last_name": target.last_name,
                    "full_name": target.full_name,
                    "mobile": target.mobile,
                    "profile_image": target.profile,
                    "bio": getattr(target, 'bio', None),
                    "city": getattr(target, 'city', None),
                    "state_province": getattr(target, 'state_province', None),
                    "country": getattr(target, 'country', None),
                    "date_of_birth": getattr(target, 'date_of_birth', None),
                    "batting_style": getattr(target, 'batting_style', None),
                    "bowling_style": getattr(target, 'bowling_style', None),
                    "role": getattr(target, 'player_role', None),
                    "created_by": target.id,
                })
                linked_player_id = new_player.id
            except Exception as _e:
                logger.warning(
                    'Legacy player auto-create failed',
                    extra={'extra_data': {'user_id': target.id, 'error': str(_e)}},
                )
        profile_data = {
            "id": target.id,
            "player_id": linked_player_id,
            "username": target.username,
            "full_name": target.full_name,
            "first_name": target.first_name,
            "last_name": target.last_name,
            "profile": target.profile,
            "bio": getattr(target, 'bio', None),
            "city": getattr(target, 'city', None),
            "state_province": getattr(target, 'state_province', None),
            "country": getattr(target, 'country', None),
            "date_of_birth": str(target.date_of_birth) if getattr(target, 'date_of_birth', None) else None,
            "batting_style": getattr(target, 'batting_style', None),
            "bowling_style": getattr(target, 'bowling_style', None),
            "player_role": getattr(target, 'player_role', None),
            "followers_count": getattr(target, 'followers_count', 0) or 0,
            "following_count": getattr(target, 'following_count', 0) or 0,
        }
        if r:
            try:
                await r.setex(cache_key, 300, _json.dumps(profile_data))
            except Exception as _e:
                logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    # Follow status is per-viewer — always live, skipped for guests.
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

    try:
        from src.database.redis.redis_client import redis_client
        r = await redis_client.get_client()
        if r:
            await r.delete(f"user:{user.id}")
    except Exception as _e:
        logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

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


# Back-compat re-export: callers still import invalidate_user_stats from this module.
from src.services.user_stats_service import UserStatsService as _UserStatsSvc


async def invalidate_user_stats(user_id: int):
    await _UserStatsSvc.invalidate(user_id)


@router.get("/me/stats")
async def get_my_stats(
    response: Response,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """User activity stats. Redis-cached for 60s. Invalidated on writes."""
    data, cached = await _UserStatsSvc.get(session, user.id)
    response.headers["X-Cache"] = "HIT" if cached else "MISS"
    return data


@router.get("/lookup-by-mobile")
@limiter.limit(RATE_LIMITS["lookup_mobile"])
async def lookup_user_by_mobile(
    request: Request,
    mobile: str = Query(..., min_length=10, max_length=10, pattern=r"^\d{10}$",
                        description="10-digit Indian mobile number"),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Return whether a user exists with the given mobile, plus public-safe
    identity fields the Add Player form can auto-fill. Auth-required and
    rate-limited so this isn't an anonymous user-enumeration vector.

    Only returns: exists, first_name, last_name, full_name, profile (photo URL).
    Never returns email, password hash, or anything sensitive.
    """
    target = await UserRepository.get_by_mobile(session, mobile.strip())
    if not target:
        return {"exists": False}
    return {
        "exists": True,
        "user_id": target.id,
        "first_name": target.first_name,
        "last_name": target.last_name,
        "full_name": target.full_name,
        "profile": target.profile,
    }


@router.post("/follow/{target_id}")
async def follow_user(
    target_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Follow a user."""
    if target_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")

    target = await session.execute(select(UserSchema.id).where(UserSchema.id == target_id))
    if not target.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    existing = await session.execute(
        select(UserFollowSchema).where(
            UserFollowSchema.follower_id == user.id,
            UserFollowSchema.following_id == target_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"status": "already_following"}

    session.add(UserFollowSchema(follower_id=user.id, following_id=target_id))

    # One UPDATE for both counters — CASE picks the right column per row.
    await session.execute(
        text(
            "UPDATE users SET "
            "following_count = following_count + CASE WHEN id = :uid THEN 1 ELSE 0 END, "
            "followers_count = followers_count + CASE WHEN id = :tid THEN 1 ELSE 0 END "
            "WHERE id IN (:uid, :tid)"
        ),
        {"uid": user.id, "tid": target_id},
    )
    await session.commit()

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
            # Paginated list caches use versioned keys like `followers:v2:{id}:*`
            # — the plain-prefix delete above won't touch them. Scan & purge.
            async for k in r.scan_iter(match=f"followers:v2:{target_id}:*", count=200):
                await r.delete(k)
            async for k in r.scan_iter(match=f"following:v2:{user.id}:*", count=200):
                await r.delete(k)
        except Exception as _e:
            logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

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
    # One UPDATE for both decrements; GREATEST clamps at 0 so a double-unfollow race can't go negative.
    await session.execute(
        text(
            "UPDATE users SET "
            "following_count = GREATEST(0, following_count - CASE WHEN id = :uid THEN 1 ELSE 0 END), "
            "followers_count = GREATEST(0, followers_count - CASE WHEN id = :tid THEN 1 ELSE 0 END) "
            "WHERE id IN (:uid, :tid)"
        ),
        {"uid": user.id, "tid": target_id},
    )
    await session.commit()

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
            # Same purge as /follow — catches the versioned pagination keys.
            async for k in r.scan_iter(match=f"followers:v2:{target_id}:*", count=200):
                await r.delete(k)
            async for k in r.scan_iter(match=f"following:v2:{user.id}:*", count=200):
                await r.delete(k)
        except Exception as _e:
            logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

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


def _decode_cursor(cursor: str | None) -> "datetime | None":
    """Parse a base64-encoded ISO timestamp cursor. Invalid cursors → start of list."""
    if not cursor:
        return None
    try:
        import base64
        from datetime import datetime
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _encode_cursor(dt) -> str:
    import base64
    return base64.urlsafe_b64encode(dt.isoformat().encode()).decode()


async def _paginate_follow_list(
    session, *, join_column, where_column, target_id: int,
    limit: int, cursor: str | None, offset: int,
):
    """Shared paginator for followers/following. Prefers `cursor` (keyset —
    constant-cost at any depth); falls back to `offset` for backward-compat
    clients but caps the scan via a guarded limit on offset."""
    from sqlalchemy.orm import load_only
    before = _decode_cursor(cursor)
    q = (
        select(UserSchema, UserFollowSchema.created_at)
        .options(load_only(UserSchema.id, UserSchema.username, UserSchema.full_name, UserSchema.profile))
        .join(UserFollowSchema, UserSchema.id == join_column)
        .where(where_column == target_id)
        .order_by(UserFollowSchema.created_at.desc())
        .limit(limit)
    )
    if before is not None:
        q = q.where(UserFollowSchema.created_at < before)
    elif offset > 0:
        q = q.offset(offset)
    rows = (await session.execute(q)).all()
    users_data = [
        {"id": u.id, "username": u.username, "full_name": u.full_name, "profile": u.profile}
        for u, _ in rows
    ]
    next_cursor = _encode_cursor(rows[-1][1]) if len(rows) == limit and rows[-1][1] else None
    return users_data, next_cursor


@router.get("/{target_id}/followers")
async def get_followers(
    response: Response,
    target_id: int,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None, description="Keyset cursor from prior X-Next-Cursor header"),
    offset: int = Query(0, ge=0, le=5000, description="Legacy offset fallback (prefer cursor)"),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Get followers of a user. Keyset-paginated (X-Next-Cursor header); Redis-cached 120s per page.
    Response body stays a flat array for backward compat with existing clients."""
    cache_key = f"followers:v2:{target_id}:{cursor or ''}:{offset}:{limit}"
    r = await _get_redis()
    payload = None

    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                payload = _json.loads(cached)
        except Exception as _e:
            logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    if payload is None:
        users_data, next_cursor = await _paginate_follow_list(
            session,
            join_column=UserFollowSchema.follower_id,
            where_column=UserFollowSchema.following_id,
            target_id=target_id, limit=limit, cursor=cursor, offset=offset,
        )
        payload = {"items": users_data, "next_cursor": next_cursor}
        if r:
            try:
                await r.setex(cache_key, 120, _json.dumps(payload))
            except Exception as _e:
                logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    # Follow-back is per-viewer → always live, skipped for guests.
    user_ids = [u["id"] for u in payload["items"]]
    following_set = set()
    if user and user_ids:
        follows_result = await session.execute(
            select(UserFollowSchema.following_id).where(
                UserFollowSchema.follower_id == user.id,
                UserFollowSchema.following_id.in_(user_ids),
            )
        )
        following_set = {row[0] for row in follows_result.all()}

    if payload.get("next_cursor"):
        response.headers["X-Next-Cursor"] = payload["next_cursor"]
    return [{**u, "is_following": u["id"] in following_set} for u in payload["items"]]


@router.get("/{target_id}/following")
async def get_following(
    response: Response,
    target_id: int,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None, description="Keyset cursor from prior X-Next-Cursor header"),
    offset: int = Query(0, ge=0, le=5000, description="Legacy offset fallback (prefer cursor)"),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Get users that target follows. Keyset-paginated (X-Next-Cursor header); Redis-cached 120s per page."""
    cache_key = f"following:v2:{target_id}:{cursor or ''}:{offset}:{limit}"
    r = await _get_redis()
    payload = None

    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                payload = _json.loads(cached)
        except Exception as _e:
            logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    if payload is None:
        users_data, next_cursor = await _paginate_follow_list(
            session,
            join_column=UserFollowSchema.following_id,
            where_column=UserFollowSchema.follower_id,
            target_id=target_id, limit=limit, cursor=cursor, offset=offset,
        )
        payload = {"items": users_data, "next_cursor": next_cursor}
        if r:
            try:
                await r.setex(cache_key, 120, _json.dumps(payload))
            except Exception as _e:
                logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

    # Follow-back is per-viewer → always live, skipped for guests.
    user_ids = [u["id"] for u in payload["items"]]
    following_set = set()
    if user and user_ids:
        follows_result = await session.execute(
            select(UserFollowSchema.following_id).where(
                UserFollowSchema.follower_id == user.id,
                UserFollowSchema.following_id.in_(user_ids),
            )
        )
        following_set = {row[0] for row in follows_result.all()}

    if payload.get("next_cursor"):
        response.headers["X-Next-Cursor"] = payload["next_cursor"]
    return [{**u, "is_following": u["id"] in following_set} for u in payload["items"]]
