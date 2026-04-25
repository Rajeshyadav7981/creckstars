import asyncio
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user_optional
from src.services.scorecard_service import ScorecardService
from src.database.redis.match_cache import MatchCache

router = APIRouter(prefix="/api/matches", tags=["Scorecards"])

# Locks to prevent thundering herd — only 1 DB query per cache key at a time
_locks: dict[str, asyncio.Lock] = {}

def _get_lock(key: str) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


@router.get("/{match_id}/scorecard")
async def get_scorecard(
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    cached = await MatchCache.get_scorecard(match_id)
    if cached:
        return cached

    async with _get_lock(f"sc:{match_id}"):
        # Double-check after acquiring lock (another request may have populated cache)
        cached = await MatchCache.get_scorecard(match_id)
        if cached:
            return cached

        scorecard = await ScorecardService.get_full_scorecard(session, match_id)
        if not scorecard:
            return {"error": "Match not found"}
        # Completed matches rarely change — cache for 10 min; live matches for 60s
        ttl = 600 if scorecard.get("status") == "completed" else 60
        await MatchCache.set_scorecard(match_id, scorecard, ttl=ttl)
        return scorecard


@router.get("/{match_id}/live-state")
async def get_live_state(
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    cached = await MatchCache.get_live_state(match_id)
    if cached:
        return cached

    async with _get_lock(f"ls:{match_id}"):
        cached = await MatchCache.get_live_state(match_id)
        if cached:
            return cached

        state = await ScorecardService.get_live_state(session, match_id)
        if not state:
            return {"error": "Match not found"}
        # Completed matches: cache 5 min; live: 5s (hot poll path)
        ttl = 300 if state.get("status") == "completed" else 5
        await MatchCache.set_live_state(match_id, state, ttl=ttl)
        return state


@router.get("/{match_id}/commentary")
async def get_commentary(
    match_id: int,
    innings_number: int = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    cache_key = f"comm:{match_id}:{innings_number}:{limit}:{offset}"
    cached = await MatchCache.get_generic(cache_key)
    if cached:
        return cached

    async with _get_lock(cache_key):
        cached = await MatchCache.get_generic(cache_key)
        if cached:
            return cached

        data = await ScorecardService.get_commentary(session, match_id, innings_number, limit, offset)
        await MatchCache.set_generic(cache_key, data, ttl=30)
        return data
