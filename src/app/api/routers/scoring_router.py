from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user
from src.services.scoring_service import ScoringService
from src.services.undo_service import UndoService
from src.database.postgres.repositories.match_repository import MatchRepository
from src.app.api.routers.models.scoring_model import ScoreDeliveryRequest, EndOverRequest, MatchStatusRequest, BroadcastMessageRequest
from src.database.redis.match_cache import MatchCache
from src.services.scorecard_service import ScorecardService
from src.app.api.rate_limiter import limiter
from src.app.api.config import RATE_LIMITS
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/matches", tags=["Scoring"])


async def _check_match_owner(session, match_id: int, user_id: int):
    match = await MatchRepository.get_by_id(session, match_id)
    if not match:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    if match.created_by != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the match creator can score")
    return match


async def _refresh_cache(session, match_id: int):
    """Write-through: populate cache after scoring mutations with smart TTL."""
    try:
        live = await ScorecardService.get_live_state(session, match_id)
        is_completed = live and live.get("status") == "completed"
        # Completed: cache 5 min; live: 5s
        await MatchCache.set_live_state(match_id, live, ttl=300 if is_completed else 5)
        sc = await ScorecardService.get_full_scorecard(session, match_id)
        # Completed: cache 10 min; live: 60s
        await MatchCache.set_scorecard(match_id, sc, ttl=600 if is_completed else 60)

        # Invalidate tournament standings cache when match completes
        if is_completed:
            match = await MatchRepository.get_by_id(session, match_id)
            if match and match.tournament_id:
                try:
                    from src.utils.cache import invalidate
                    await invalidate(f"tournament:{match.tournament_id}")
                    await MatchCache.set_generic(f"standings:{match.tournament_id}", None, ttl=1)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Cache refresh failed for match {match_id}: {e}")


@router.post("/{match_id}/score")
@limiter.limit(RATE_LIMITS["score_delivery"])
async def score_delivery(
    request: Request,
    match_id: int,
    req: ScoreDeliveryRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await _check_match_owner(session, match_id, user.id)
    result = await ScoringService.record_delivery(session, match_id, user.id, req.model_dump())
    await _refresh_cache(session, match_id)
    return result


@router.post("/{match_id}/undo")
@limiter.limit(RATE_LIMITS["undo"])
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
async def end_over(
    match_id: int,
    req: EndOverRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await _check_match_owner(session, match_id, user.id)
    result = await ScoringService.end_over(session, match_id, req.next_bowler_id)
    await _refresh_cache(session, match_id)
    return result


@router.post("/{match_id}/end-innings")
async def end_innings(
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await _check_match_owner(session, match_id, user.id)
    result = await ScoringService.end_innings(session, match_id)
    await _refresh_cache(session, match_id)
    return result


@router.post("/{match_id}/end-match")
async def end_match(
    match_id: int,
    force_tie: bool = False,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await _check_match_owner(session, match_id, user.id)
    result = await ScoringService.end_match(session, match_id, force_tie=force_tie)
    if not result.get("is_tied"):
        await MatchCache.invalidate_match(match_id)
    return result


@router.post("/{match_id}/revert")
async def revert_match(
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Revert a completed match: reopen match & innings, undo last ball."""
    await _check_match_owner(session, match_id, user.id)
    from src.services.revert_service import RevertService
    result = await RevertService.revert_completed_match(session, match_id, user.id)
    await MatchCache.invalidate_match(match_id)
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
    # Broadcast via WebSocket to all viewers
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
    return {"status": "no_result", "result_summary": reason}
