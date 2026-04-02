from fastapi import APIRouter, Depends, Query
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user
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
    return {"id": m.id, "match_code": m.match_code, "status": m.status, "team_a_id": m.team_a_id, "team_b_id": m.team_b_id, "overs": m.overs}


@router.get("")
@limiter.limit(RATE_LIMITS["list_matches"])
async def list_matches(
    request: Request,
    status: str = Query(None),
    tournament_id: int = Query(None),
    created_by: int = Query(None, description="Filter by creator user ID"),
    search: str = Query(None, description="Search matches by team name or code"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    cache_key = f"matches:u{created_by or user.id}:s{status}:t{tournament_id}:l{limit}:o{offset}"

    async def _fetch():
        matches = await MatchService.get_matches(
            session, status_filter=status, tournament_id=tournament_id,
            search=search, created_by=created_by, limit=limit, offset=offset,
        )
        team_ids = set()
        for m in matches:
            if m.team_a_id: team_ids.add(m.team_a_id)
            if m.team_b_id: team_ids.add(m.team_b_id)
        team_names = {}
        if team_ids:
            res = await session.execute(select(TeamSchema).where(TeamSchema.id.in_(team_ids)))
            for t in res.scalars().all():
                team_names[t.id] = t.name
        return [{
            "id": m.id, "match_code": m.match_code, "status": m.status,
            "team_a_id": m.team_a_id, "team_b_id": m.team_b_id,
            "team_a_name": team_names.get(m.team_a_id), "team_b_name": team_names.get(m.team_b_id),
            "overs": m.overs, "tournament_id": m.tournament_id,
            "match_date": str(m.match_date) if m.match_date else None,
            "result_summary": m.result_summary, "winner_id": m.winner_id,
            "created_by": m.created_by,
        } for m in matches]

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
    user=Depends(get_current_user),
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
    user=Depends(get_current_user),
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
    # Load venue details
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
        except Exception:
            pass
    asyncio.create_task(_notify_squad())

    return {"message": "Squad set successfully"}


@router.get("/{match_id}/squads/{team_id}")
async def get_squad(
    match_id: int,
    team_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    return await MatchService.get_squad(session, match_id, team_id)


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
    # Invalidate cached live-state so the new innings is returned immediately
    await MatchCache.invalidate_match(match_id)
    return {
        "innings_id": innings.id, "innings_number": innings.innings_number,
        "batting_team_id": innings.batting_team_id, "status": innings.status,
    }
