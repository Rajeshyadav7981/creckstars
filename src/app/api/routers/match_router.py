from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user, get_current_user_optional
from src.services.match_service import MatchService
from src.database.postgres.schemas.team_schema import TeamSchema
from src.database.postgres.schemas.tournament_schema import TournamentSchema
from src.database.postgres.repositories.match_repository import MatchRepository
from src.database.redis.match_cache import MatchCache
from src.utils.cache import cached
from src.app.api.routers.models.match_model import (
    CreateMatchRequest, TossRequest, SetSquadRequest, StartInningsRequest,
)
from src.app.api.rate_limiter import limiter
from src.app.api.config import RATE_LIMITS
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/matches", tags=["Matches"])


@router.post("")
async def create_match(
    req: CreateMatchRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    m = await MatchService.create_match(
        session, user.id,
        tournament_id=req.tournament_id, team_a_id=req.team_a_id, team_b_id=req.team_b_id,
        venue_id=req.venue_id, match_date=req.match_date, overs=req.overs,
        match_type=req.match_type, time_slot=req.time_slot,
        stage_id=req.stage_id, group_id=req.group_id,
    )
    from src.app.api.routers.user_router import invalidate_user_stats
    await invalidate_user_stats(user.id)
    return {"id": m.id, "match_code": m.match_code, "status": m.status, "team_a_id": m.team_a_id, "team_b_id": m.team_b_id, "overs": m.overs}


@router.get("")
@limiter.limit(RATE_LIMITS["list_matches"])
async def list_matches(
    request: Request,
    status: str = Query(None),
    tournament_id: int = Query(None),
    stage_id: int = Query(None, description="Filter by tournament stage"),
    created_by: int = Query(None, description="Filter by creator user ID"),
    for_user: int = Query(None, description="Fetch matches created OR played by this user (returns role field)"),
    role: str = Query(None, description="When for_user is set, filter to 'organized' or 'played' role only"),
    search: str = Query(None, description="Search matches by team name or code"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    uid = (user.id if user else 0)
    cache_key = f"matches:u{for_user or created_by or uid}:s{status}:t{tournament_id}:st{stage_id}:r{role}:l{limit}:o{offset}"

    async def _fetch():
        matches = await MatchService.get_matches(
            session, status_filter=status, tournament_id=tournament_id,
            search=search, created_by=created_by if not for_user else None,
            stage_id=stage_id, for_user=for_user, role=role,
            limit=limit, offset=offset,
        )
        team_ids = set()
        match_ids = []
        for m in matches:
            match_ids.append(m.id)
            if m.team_a_id: team_ids.add(m.team_a_id)
            if m.team_b_id: team_ids.add(m.team_b_id)
        team_names = {}
        if team_ids:
            res = await session.execute(select(TeamSchema).where(TeamSchema.id.in_(team_ids)))
            for t in res.scalars().all():
                team_names[t.id] = t.name

        # Batch-load innings scores so list cards can show runs/wickets/overs.
        # One query for all matches instead of N per match.
        from src.database.postgres.schemas.innings_schema import InningsSchema
        scores = {}
        if match_ids:
            inn_res = await session.execute(
                select(
                    InningsSchema.match_id, InningsSchema.batting_team_id,
                    InningsSchema.total_runs, InningsSchema.total_wickets, InningsSchema.total_overs,
                ).where(InningsSchema.match_id.in_(match_ids))
            )
            for row in inn_res.all():
                scores.setdefault(row.match_id, {})[row.batting_team_id] = {
                    "runs": row.total_runs, "wickets": row.total_wickets, "overs": row.total_overs,
                }

        from src.database.postgres.schemas.venue_schema import VenueSchema
        venue_ids = {m.venue_id for m in matches if m.venue_id}
        venue_names = {}
        if venue_ids:
            v_res = await session.execute(
                select(VenueSchema.id, VenueSchema.name).where(VenueSchema.id.in_(venue_ids))
            )
            venue_names = {vid: vname for vid, vname in v_res.all()}

        result = []
        for m in matches:
            ms = scores.get(m.id, {})
            sa = ms.get(m.team_a_id, {})
            sb = ms.get(m.team_b_id, {})
            result.append({
                "id": m.id, "match_code": m.match_code, "status": m.status,
                "team_a_id": m.team_a_id, "team_b_id": m.team_b_id,
                "team_a_name": team_names.get(m.team_a_id), "team_b_name": team_names.get(m.team_b_id),
                "team_a_runs": sa.get("runs"), "team_a_wickets": sa.get("wickets"), "team_a_overs": sa.get("overs"),
                "team_b_runs": sb.get("runs"), "team_b_wickets": sb.get("wickets"), "team_b_overs": sb.get("overs"),
                "overs": m.overs, "tournament_id": m.tournament_id,
                "stage_id": m.stage_id, "group_id": m.group_id, "match_number": m.match_number,
                "match_date": str(m.match_date) if m.match_date else None,
                "venue_name": venue_names.get(m.venue_id),
                "result_summary": m.result_summary, "winner_id": m.winner_id,
                "created_by": m.created_by,
                "role": getattr(m, '_role', None),
            })
        return result

    # Skip cache for search queries (too many variations)
    if search:
        return await _fetch()
    return await cached(cache_key, ttl=30, fetcher=_fetch)


@router.get("/nearby")
async def nearby_matches(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    radius: float = Query(50, ge=1, le=500, description="Radius in km"),
    status: str = Query(None, description="Filter by match status"),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Find upcoming/live matches at venues within a radius (km)."""
    rows = await MatchRepository.get_nearby(session, lat, lng, radius)
    return [
        {
            "id": r["id"], "match_code": r["match_code"], "status": r["status"],
            "team_a_name": r["team_a_name"], "team_b_name": r["team_b_name"],
            "overs": r["overs"], "match_date": str(r["match_date"]) if r["match_date"] else None,
            "venue_name": r["venue_name"], "distance_km": round(r["distance_km"], 2),
        }
        for r in rows
    ]


@router.get("/{match_id}")
async def get_match(
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    from src.database.redis.match_cache import MatchCache
    cached_data = await MatchCache.get_generic(f"match_detail:{match_id}")
    if cached_data:
        return cached_data
    m = await MatchService.get_match(session, match_id)
    # Batch-load related entities in one query instead of individual session.get()
    ids_to_load = [i for i in [m.team_a_id, m.team_b_id] if i]
    teams = {}
    if ids_to_load:
        res = await session.execute(select(TeamSchema).where(TeamSchema.id.in_(ids_to_load)))
        for t in res.scalars().all():
            teams[t.id] = t
    team_a = teams.get(m.team_a_id)
    team_b = teams.get(m.team_b_id)
    tournament = await session.get(TournamentSchema, m.tournament_id) if m.tournament_id else None
    venue = None
    if m.venue_id:
        from src.database.postgres.schemas.venue_schema import VenueSchema
        venue = await session.get(VenueSchema, m.venue_id)
    result = {
        "id": m.id, "match_code": m.match_code, "status": m.status,
        "team_a_id": m.team_a_id, "team_b_id": m.team_b_id,
        "team_a_name": team_a.name if team_a else None,
        "team_b_name": team_b.name if team_b else None,
        "team_a_short": team_a.short_name if team_a else None,
        "team_b_short": team_b.short_name if team_b else None,
        "team_a_color": team_a.color if team_a else None,
        "team_b_color": team_b.color if team_b else None,
        "overs": m.overs, "tournament_id": m.tournament_id,
        "tournament_name": tournament.name if tournament else None,
        "toss_winner_id": m.toss_winner_id, "toss_decision": m.toss_decision,
        "winner_id": m.winner_id, "result_summary": m.result_summary,
        "current_innings": m.current_innings, "created_by": m.created_by,
        "match_date": m.match_date.isoformat() if m.match_date else None,
        "match_type": m.match_type,
        "time_slot": m.time_slot,
        "match_number": m.match_number,
        "venue_id": m.venue_id,
        "result_type": getattr(m, 'result_type', None),
        "venue_name": venue.name if venue else None,
        "venue_city": venue.city if venue else None,
        "venue_ground_type": venue.ground_type if venue else None,
        "venue_address": venue.address if venue else None,
    }
    ttl = 10 if m.status in ('in_progress', 'live') else 120
    await MatchCache.set_generic(f"match_detail:{match_id}", result, ttl=ttl)
    return result


class UpdateMatchRequest(BaseModel):
    """Editable match fields. Only allowed before play has started."""
    overs: Optional[int] = None
    match_date: Optional[date] = None
    time_slot: Optional[str] = None


@router.patch("/{match_id}")
async def update_match(
    match_id: int,
    req: UpdateMatchRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Update editable fields of a match. Only allowed while the match is
    still `upcoming` (toss/innings haven't been recorded yet)."""
    m = await MatchRepository.get_by_id(session, match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.created_by and m.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the match creator can edit it")
    if m.status not in (None, "upcoming"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit match — already in '{m.status}' state",
        )
    if req.overs is not None:
        if req.overs < 1 or req.overs > 50:
            raise HTTPException(status_code=400, detail="Overs must be between 1 and 50")
    updates = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        return {"id": m.id, "overs": m.overs, "match_date": str(m.match_date) if m.match_date else None, "time_slot": m.time_slot}
    updated = await MatchRepository.update(session, match_id, updates)
    await session.commit()
    # If the row wasn't in the identity map, update() returns None — re-read.
    if updated is None:
        updated = await MatchRepository.get_by_id(session, match_id)
    await MatchCache.invalidate_match(match_id)
    # Also invalidate the parent tournament's cached detail so the new value
    # is visible in the tournament screen immediately.
    if updated.tournament_id:
        from src.utils.cache import invalidate as _invalidate_cache
        await _invalidate_cache(f"tournament:{updated.tournament_id}")
    return {
        "id": updated.id,
        "overs": updated.overs,
        "match_date": str(updated.match_date) if updated.match_date else None,
        "time_slot": updated.time_slot,
    }


@router.post("/{match_id}/toss")
async def record_toss(
    match_id: int,
    req: TossRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    m = await MatchService.record_toss(session, match_id, req.toss_winner_id, req.toss_decision, user_id=user.id)
    await MatchCache.invalidate_match(match_id)
    return {"message": "Toss recorded", "toss_winner_id": m.toss_winner_id, "toss_decision": m.toss_decision}


@router.post("/{match_id}/squads")
async def set_squad(
    match_id: int,
    req: SetSquadRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    players = [{"player_id": p.player_id, "batting_order": p.batting_order} for p in req.players]
    await MatchService.set_squad(session, match_id, req.team_id, players, user_id=user.id)
    # Drop the cached squad row so the next GET reflects the new selection.
    from src.utils.cache import invalidate as _invalidate_cache
    await _invalidate_cache(f"squad:{match_id}:{req.team_id}")

    # Invalidate stats cache for all users whose players were added to the squad
    from src.app.api.routers.user_router import invalidate_user_stats
    player_ids = [p.player_id for p in req.players]
    if player_ids:
        from src.database.postgres.schemas.player_schema import PlayerSchema as _PS
        res = await session.execute(
            select(_PS.user_id).where(_PS.id.in_(player_ids), _PS.user_id.isnot(None))
        )
        for (uid,) in res.all():
            await invalidate_user_stats(uid)
    await invalidate_user_stats(user.id)

    # Send push notification to squad players (fire-and-forget, own session)
    import asyncio
    team_id_for_notify = req.team_id
    async def _notify_squad():
        try:
            from src.database.postgres.db import db
            from src.services.notification_service import NotificationService
            async with db.AsyncSessionLocal() as notify_session:
                from src.database.postgres.schemas.team_schema import TeamSchema as TS
                team = await notify_session.get(TS, team_id_for_notify)
                team_name = team.name if team else "your team"
            tokens = await NotificationService.get_all_match_tokens(match_id)
            if tokens:
                await NotificationService.send_expo_push(
                    tokens,
                    "Squad Selected",
                    f"You've been selected in {team_name}'s playing XI!",
                    {"match_id": match_id, "type": "squad_set"},
                )
        except Exception as _e:
            logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})
    asyncio.create_task(_notify_squad())

    return {"message": "Squad set successfully"}


@router.get("/{match_id}/squads/{team_id}")
async def get_squad(
    match_id: int,
    team_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    # Squads are read-heavy during innings setup (squad pickers, openers
    # selection, batting-order screens). The data only changes when the
    # creator runs `set_squad`, which already invalidates the match cache.
    # We use a separate cache key per (match, team) so the squad can be
    # served from Redis on subsequent reads.
    async def _fetch():
        return await MatchService.get_squad(session, match_id, team_id)
    return await cached(f"squad:{match_id}:{team_id}", ttl=60, fetcher=_fetch)


@router.post("/{match_id}/start-innings")
async def start_innings(
    match_id: int,
    req: StartInningsRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    innings = await MatchService.start_innings(
        session, match_id, req.batting_team_id, req.striker_id, req.non_striker_id, req.bowler_id, user_id=user.id,
    )
    await MatchCache.invalidate_match(match_id)
    return {
        "innings_id": innings.id, "innings_number": innings.innings_number,
        "batting_team_id": innings.batting_team_id, "status": innings.status,
    }
