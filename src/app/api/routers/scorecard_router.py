import asyncio
import weakref
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user_optional
from src.services.scorecard_service import ScorecardService
from src.database.redis.match_cache import MatchCache

router = APIRouter(prefix="/api/matches", tags=["Scorecards"])

_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()

def _get_lock(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


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
        cached = await MatchCache.get_scorecard(match_id)
        if cached:
            return cached

        lock_name = f"sc:{match_id}"
        got_lock = await MatchCache.try_acquire_refresh_lock(lock_name, ttl=10)
        if not got_lock:
            for _ in range(8):
                await asyncio.sleep(0.05)
                cached = await MatchCache.get_scorecard(match_id)
                if cached:
                    return cached

        try:
            scorecard = await ScorecardService.get_full_scorecard(session, match_id)
            if not scorecard:
                return {"error": "Match not found"}
            ttl = 600 if scorecard.get("status") == "completed" else 60
            await MatchCache.set_scorecard(match_id, scorecard, ttl=ttl)
            return scorecard
        finally:
            if got_lock:
                await MatchCache.release_refresh_lock(lock_name)


@router.get("/{match_id}/live-state")
async def get_live_state(
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    cached = await MatchCache.get_live_state(match_id)
    if cached:
        return cached

    # Per-process lock first (cheap; coalesces requests on this worker).
    async with _get_lock(f"ls:{match_id}"):
        cached = await MatchCache.get_live_state(match_id)
        if cached:
            return cached

        # Cross-process single-flight via Redis SETNX. Under heavy load with
        # many workers, this stops every worker from racing to the DB on a
        # shared cache miss.
        lock_name = f"ls:{match_id}"
        got_lock = await MatchCache.try_acquire_refresh_lock(lock_name, ttl=10)
        if not got_lock:
            # Another worker is already computing — give it a brief moment, then
            # serve whatever it wrote. If it's still not there, fall through and
            # compute ourselves so a stuck owner can't starve clients.
            for _ in range(6):
                await asyncio.sleep(0.05)
                cached = await MatchCache.get_live_state(match_id)
                if cached:
                    return cached

        try:
            state = await ScorecardService.get_live_state(session, match_id)
            if not state:
                return {"error": "Match not found"}
            # Completed matches: cache 5 min; live: 5s (hot poll path)
            ttl = 300 if state.get("status") == "completed" else 5
            await MatchCache.set_live_state(match_id, state, ttl=ttl)
            return state
        finally:
            if got_lock:
                await MatchCache.release_refresh_lock(lock_name)


@router.get("/{match_id}/commentary")
async def get_commentary(
    match_id: int,
    innings_number: int = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000),  # commentary deep-scroll capped — past 1000 callers should keyset by sequence
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
