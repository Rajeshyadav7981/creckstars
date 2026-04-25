import asyncio as _asyncio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db, db as _db
from src.utils.security import get_current_user
from src.services.scoring_service import ScoringService
from src.services.undo_service import UndoService
from src.database.postgres.repositories.match_repository import MatchRepository
from src.app.api.routers.models.scoring_model import ScoreDeliveryRequest, EndOverRequest, MatchStatusRequest, BroadcastMessageRequest
from src.database.redis.match_cache import MatchCache
from src.services.scorecard_service import ScorecardService
from src.app.api.rate_limiter import limiter
from src.app.api.config import RATE_LIMITS
from src.utils.idempotency import idempotent
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/matches", tags=["Scoring"])


async def _check_match_owner(session, match_id: int, user_id: int):
    match = await MatchRepository.get_by_id(session, match_id)
    if not match:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    # Allow match creator or designated scorer
    if match.created_by != user_id and getattr(match, 'scorer_user_id', None) != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the match creator can score")
    return match


async def _refresh_cache(session, match_id: int):
    """Fast cache refresh on the hot path after scoring mutations.

    Keeps only the cheap live_state refresh inline (viewers poll this
    every ~1s and need it fresh). The full scorecard is intentionally
    NOT rebuilt here — we invalidate it and let the next viewer request
    repopulate it lazily. Rebuilding the scorecard on every delivery
    turned out to be the dominant cost (~2s per ball) and is wasted work.
    """
    try:
        live = await ScorecardService.get_live_state(session, match_id)
        is_completed = live and live.get("status") == "completed"
        # Completed: cache 5 min; live: 5s
        await MatchCache.set_live_state(match_id, live, ttl=300 if is_completed else 5)

        # Invalidate the heavy caches — first read after mutation repopulates them
        await MatchCache.set_scorecard(match_id, None, ttl=1)
        await MatchCache.set_generic(f"match_detail:{match_id}", None, ttl=1)

        if is_completed:
            match = await MatchRepository.get_by_id(session, match_id)
            if match and match.tournament_id:
                try:
                    from src.utils.cache import invalidate
                    await invalidate(f"tournament:{match.tournament_id}")
                    await MatchCache.set_generic(f"standings:{match.tournament_id}", None, ttl=1)
                except Exception as _e:
                    logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})

            # Invalidate stats cache for match creator + all squad members
            # (played/created completed counts changed). Also invalidate per-player
            # career stats cache so PlayerProfile reflects the new innings/spell.
            try:
                from src.app.api.routers.user_router import invalidate_user_stats
                from src.database.postgres.schemas.match_squad_schema import MatchSquadSchema as _MSQ
                from src.database.postgres.schemas.player_schema import PlayerSchema as _PS
                from sqlalchemy import select as _sel
                if match:
                    await invalidate_user_stats(match.created_by)
                    res = await session.execute(
                        _sel(_MSQ.player_id, _PS.user_id)
                        .join(_PS, _PS.id == _MSQ.player_id)
                        .where(_MSQ.match_id == match_id)
                    )
                    seen_uids = set()
                    seen_pids = set()
                    for (pid, uid) in res.all():
                        if uid and uid not in seen_uids:
                            seen_uids.add(uid)
                            await invalidate_user_stats(uid)
                        if pid and pid not in seen_pids:
                            seen_pids.add(pid)
                            await MatchCache.set_generic(f"player_stats:{pid}", None, ttl=1)
            except Exception as _e:
                logger.warning('Non-critical cache/invalidation failed', extra={'extra_data': {'error': str(_e)}})
    except Exception as e:
        logger.warning(f"Cache refresh failed for match {match_id}: {e}")


async def _refresh_cache_background(match_id: int):
    """Background-task variant that opens its own DB session.

    Used to move cache refresh off the request path entirely so the
    scoring response returns as soon as the delivery commit lands.
    """
    try:
        async with _db.AsyncSessionLocal() as bg_session:
            await _refresh_cache(bg_session, match_id)
    except Exception as e:
        logger.warning(f"Background cache refresh failed for match {match_id}: {e}")


@router.post("/{match_id}/score")
@limiter.limit(RATE_LIMITS["score_delivery"])
@idempotent(ttl_seconds=600)
async def score_delivery(
    request: Request,
    match_id: int,
    req: ScoreDeliveryRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await _check_match_owner(session, match_id, user.id)
    result = await ScoringService.record_delivery(session, match_id, user.id, req.model_dump())
    # Fire-and-forget: refresh cache in the background without blocking
    # the worker (FastAPI BackgroundTasks would serialize on a single worker).
    _asyncio.create_task(_refresh_cache_background(match_id))
    return result


@router.post("/{match_id}/undo")
@limiter.limit(RATE_LIMITS["undo"])
@idempotent(ttl_seconds=600)
async def undo_last_ball(
    request: Request,
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await _check_match_owner(session, match_id, user.id)
    result = await UndoService.undo_last_ball(session, match_id, user.id)
    await MatchCache.invalidate_match(match_id)
    return result


@router.post("/{match_id}/end-over")
@limiter.limit(RATE_LIMITS["end_over"])
@idempotent(ttl_seconds=600)
async def end_over(
    request: Request,
    match_id: int,
    req: EndOverRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await _check_match_owner(session, match_id, user.id)
    result = await ScoringService.end_over(session, match_id, req.next_bowler_id)
    _asyncio.create_task(_refresh_cache_background(match_id))
    return result


@router.post("/{match_id}/end-innings")
@limiter.limit(RATE_LIMITS["end_innings"])
async def end_innings(
    request: Request,
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await _check_match_owner(session, match_id, user.id)
    result = await ScoringService.end_innings(session, match_id)
    _asyncio.create_task(_refresh_cache_background(match_id))
    return result


@router.post("/{match_id}/end-match")
@limiter.limit(RATE_LIMITS["end_match"])
async def end_match(
    request: Request,
    match_id: int,
    force_tie: bool = False,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    match = await _check_match_owner(session, match_id, user.id)
    result = await ScoringService.end_match(session, match_id, force_tie=force_tie)
    if not result.get("is_tied"):
        await MatchCache.invalidate_match(match_id)
        # Also wipe the matches-list cache for this user so the home tab
        # immediately reflects the new "completed" status (no stale cards).
        from src.utils.cache import invalidate_pattern
        await invalidate_pattern(f"matches:u{match.created_by}:*")
        await invalidate_pattern(f"matches:u{user.id}:*")
    return result


@router.post("/{match_id}/swap-batters")
async def swap_batters(
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await _check_match_owner(session, match_id, user.id)
    result = await ScoringService.swap_batters(session, match_id)
    await _refresh_cache(session, match_id)
    return result


@router.post("/{match_id}/revert")
@limiter.limit(RATE_LIMITS["revert"])
async def revert_match(
    request: Request,
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Revert a completed match: reopen match & innings, undo last ball."""
    match = await _check_match_owner(session, match_id, user.id)
    from src.services.revert_service import RevertService
    result = await RevertService.revert_completed_match(session, match_id, user.id)
    await MatchCache.invalidate_match(match_id)
    # Status flipped back from completed → live, so home cards are stale
    from src.utils.cache import invalidate_pattern
    await invalidate_pattern(f"matches:u{match.created_by}:*")
    await invalidate_pattern(f"matches:u{user.id}:*")
    return result


@router.post("/{match_id}/broadcast")
@limiter.limit(RATE_LIMITS["broadcast"])
async def broadcast_message(
    request: Request,
    match_id: int,
    req: BroadcastMessageRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Admin broadcasts a message to all match viewers (e.g. 'Innings Break', 'Rain Delay')."""
    await _check_match_owner(session, match_id, user.id)
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(message) > 200:
        raise HTTPException(status_code=400, detail="Message too long (max 200 chars)")
    # Store in Redis (auto-expires in 10 min)
    await MatchCache.set_broadcast_message(match_id, message)
    await MatchCache.publish_update(match_id, "broadcast", {"message": message})
    return {"status": "ok", "message": message}


@router.delete("/{match_id}/broadcast")
async def clear_broadcast(
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Admin clears the broadcast message."""
    await _check_match_owner(session, match_id, user.id)
    await MatchCache.clear_broadcast_message(match_id)
    await MatchCache.publish_update(match_id, "broadcast", {"message": None})
    return {"status": "ok"}


@router.get("/{match_id}/broadcast")
async def get_broadcast(match_id: int):
    """Get the current broadcast message for a match."""
    msg = await MatchCache.get_broadcast_message(match_id)
    return {"message": msg}


@router.post("/{match_id}/abandon")
async def abandon_match(
    match_id: int,
    req: MatchStatusRequest = None,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Mark a match as abandoned. No result, no points."""
    await _check_match_owner(session, match_id, user.id)
    reason = req.reason if req and req.reason else "Match abandoned"
    match = await MatchRepository.get_by_id(session, match_id)
    await MatchRepository.update(session, match_id, {
        "status": "completed", "result_type": "abandoned",
        "winner_id": None, "result_summary": reason,
    })
    await session.commit()
    from src.services.tournament_stage_service import TournamentStageService
    try:
        await TournamentStageService.on_match_completed(session, match_id)
    except Exception as e:
        logger.error(f"Stage progression failed after abandon for match {match_id}: {e}")
    await MatchCache.invalidate_match(match_id)
    from src.utils.cache import invalidate_pattern
    if match:
        await invalidate_pattern(f"matches:u{match.created_by}:*")
    await invalidate_pattern(f"matches:u{user.id}:*")
    return {"status": "abandoned", "result_summary": reason}


@router.post("/{match_id}/no-result")
async def no_result_match(
    match_id: int,
    req: MatchStatusRequest = None,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Mark a match as no result (rain, etc.)."""
    await _check_match_owner(session, match_id, user.id)
    reason = req.reason if req and req.reason else "No result"
    await MatchRepository.update(session, match_id, {
        "status": "completed", "result_type": "no_result",
        "winner_id": None, "result_summary": reason,
    })
    await session.commit()
    from src.services.tournament_stage_service import TournamentStageService
    try:
        await TournamentStageService.on_match_completed(session, match_id)
    except Exception as e:
        logger.error(f"Stage progression failed after no-result for match {match_id}: {e}")
    await MatchCache.invalidate_match(match_id)
    from src.utils.cache import invalidate_pattern
    await invalidate_pattern(f"matches:u{user.id}:*")
    return {"status": "no_result", "result_summary": reason}
