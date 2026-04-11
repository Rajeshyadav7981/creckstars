from typing import Optional
from datetime import date
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user, get_current_user_optional
from src.services.tournament_service import TournamentService
from src.services.tournament_stage_service import TournamentStageService
from src.database.postgres.repositories.match_repository import MatchRepository
from src.database.postgres.repositories.tournament_stage_repository import TournamentStageRepository
from src.app.api.routers.models.tournament_model import (
    CreateTournamentRequest, AddTeamToTournamentRequest,
    SetupStagesRequest, SetupGroupsRequest,
    UpdateTournamentRequest, QualificationRuleRequest,
    OverrideMatchRequest,
)
from src.database.redis.leaderboard_cache import LeaderboardCache
from src.app.api.rate_limiter import limiter
from src.app.api.config import RATE_LIMITS
from starlette.requests import Request
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ScheduleMatchItem(BaseModel):
    match_id: int
    match_date: Optional[date] = None
    time_slot: Optional[str] = None


class ScheduleMatchesRequest(BaseModel):
    schedule: list[ScheduleMatchItem]

router = APIRouter(prefix="/api/tournaments", tags=["Tournaments"])


@router.post("")
@limiter.limit(RATE_LIMITS["create_tournament"])
async def create_tournament(
    request: Request,
    req: CreateTournamentRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    extra = {}
    if hasattr(req, 'points_per_win') and req.points_per_win is not None:
        extra['points_per_win'] = req.points_per_win
    if hasattr(req, 'points_per_draw') and req.points_per_draw is not None:
        extra['points_per_draw'] = req.points_per_draw
    if hasattr(req, 'points_per_no_result') and req.points_per_no_result is not None:
        extra['points_per_no_result'] = req.points_per_no_result
    if hasattr(req, 'has_third_place_playoff') and req.has_third_place_playoff is not None:
        extra['has_third_place_playoff'] = req.has_third_place_playoff
    t = await TournamentService.create_tournament(
        session, user.id,
        name=req.name, tournament_type=req.tournament_type,
        overs_per_match=req.overs_per_match, ball_type=req.ball_type,
        start_date=req.start_date, end_date=req.end_date, venue_id=req.venue_id,
        organizer_name=req.organizer_name, location=req.location,
        entry_fee=req.entry_fee, prize_pool=req.prize_pool,
        banner_url=req.banner_url, **extra,
    )
    return {"id": t.id, "tournament_code": t.tournament_code, "name": t.name, "status": t.status, "tournament_type": t.tournament_type}


@router.get("")
@limiter.limit(RATE_LIMITS["list_tournaments"])
async def list_tournaments(
    request: Request,
    status: str = Query(None),
    created_by: int = Query(None, description="Filter by creator user ID"),
    for_user: int = Query(None, description="Fetch tournaments created OR played by this user (returns role field)"),
    search: str = Query(None, description="Search tournaments by name or code"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    tournaments = await TournamentService.get_tournaments(
        session, status_filter=status,
        created_by=created_by if not for_user else None,
        search=search, for_user=for_user, limit=limit, offset=offset,
    )

    # Lightweight per-tournament summary: stage count + match counts.
    # Two batched aggregate queries instead of N+1.
    from sqlalchemy import func as _func, select as _select, case as _case
    from src.database.postgres.schemas.tournament_stage_schema import TournamentStageSchema
    from src.database.postgres.schemas.match_schema import MatchSchema as _MS
    tids = [t.id for t in tournaments]
    stage_counts = {}
    matches_total = {}
    matches_completed = {}
    if tids:
        # Stage counts per tournament
        sres = await session.execute(
            _select(TournamentStageSchema.tournament_id, _func.count(TournamentStageSchema.id))
            .where(TournamentStageSchema.tournament_id.in_(tids))
            .group_by(TournamentStageSchema.tournament_id)
        )
        for tid, cnt in sres.all():
            stage_counts[tid] = cnt or 0
        # Match counts per tournament (total + completed)
        mres = await session.execute(
            _select(
                _MS.tournament_id,
                _func.count(_MS.id).label("total"),
                _func.sum(_case((_MS.status == "completed", 1), else_=0)).label("completed"),
            )
            .where(_MS.tournament_id.in_(tids))
            .group_by(_MS.tournament_id)
        )
        for tid, total, completed in mres.all():
            matches_total[tid] = total or 0
            matches_completed[tid] = int(completed or 0)

    return [{
        "id": t.id, "tournament_code": t.tournament_code, "name": t.name, "status": t.status,
        "tournament_type": t.tournament_type, "overs_per_match": t.overs_per_match,
        "ball_type": t.ball_type,
        "start_date": str(t.start_date) if t.start_date else None,
        "end_date": str(t.end_date) if t.end_date else None,
        "organizer_name": t.organizer_name,
        "location": t.location,
        "entry_fee": t.entry_fee,
        "prize_pool": t.prize_pool,
        "created_at": str(t.created_at) if t.created_at else None,
        "created_by": t.created_by,
        # Stage / match summary so list cards can show progress at a glance
        "stages_count": stage_counts.get(t.id, 0),
        "matches_total": matches_total.get(t.id, 0),
        "matches_completed": matches_completed.get(t.id, 0),
        "role": getattr(t, '_role', None),
    } for t in tournaments]


@router.get("/{tournament_id}")
async def get_tournament(
    tournament_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    from src.utils.cache import cached as _cached

    async def _fetch_detail():
        return await _build_tournament_response(session, tournament_id)

    # Cache tournament detail for 15s (invalidated on match/stage changes)
    return await _cached(f"tournament:{tournament_id}", ttl=30, fetcher=_fetch_detail)


async def _build_tournament_response(session, tournament_id):
    """Build the full tournament detail response — extracted for caching."""
    detail = await TournamentService.get_tournament_detail(session, tournament_id)
    innings_by_match = detail.get("innings_by_match", {})
    stage_map = detail.get("stage_map", {})
    group_map = detail.get("group_map", {})

    def serialize_match(m):
        match_data = {
            "id": m.id, "status": m.status, "team_a_id": m.team_a_id, "team_b_id": m.team_b_id,
            "overs": m.overs, "winner_id": m.winner_id, "result_summary": m.result_summary,
            "match_type": m.match_type, "current_innings": m.current_innings,
            "match_date": str(m.match_date) if m.match_date else None,
            "time_slot": m.time_slot,
            "match_number": m.match_number,
            "stage_id": m.stage_id,
            "group_id": m.group_id,
            "stage_name": stage_map.get(m.stage_id, None) if m.stage_id else None,
            "group_name": group_map.get(m.group_id, None) if m.group_id else None,
        }
        # Enrich with innings score data
        innings_list = innings_by_match.get(m.id, [])
        for inn in innings_list:
            if inn.batting_team_id == m.team_a_id:
                match_data["team_a_runs"] = inn.total_runs
                match_data["team_a_wickets"] = inn.total_wickets
                match_data["team_a_overs"] = inn.total_overs
            elif inn.batting_team_id == m.team_b_id:
                match_data["team_b_runs"] = inn.total_runs
                match_data["team_b_wickets"] = inn.total_wickets
                match_data["team_b_overs"] = inn.total_overs
        # Current batting team
        if innings_list:
            active = [i for i in innings_list if i.status == "in_progress"]
            if active:
                match_data["batting_team_id"] = active[0].batting_team_id
        return match_data

    # Load stages
    stages = await TournamentStageService.get_stages_with_details(session, tournament_id)

    return {
        "tournament": {
            "id": detail["tournament"].id, "tournament_code": detail["tournament"].tournament_code, "name": detail["tournament"].name,
            "status": detail["tournament"].status, "tournament_type": detail["tournament"].tournament_type,
            "overs_per_match": detail["tournament"].overs_per_match,
            "ball_type": detail["tournament"].ball_type,
            "start_date": str(detail["tournament"].start_date) if detail["tournament"].start_date else None,
            "end_date": str(detail["tournament"].end_date) if detail["tournament"].end_date else None,
            "organizer_name": detail["tournament"].organizer_name,
            "location": detail["tournament"].location,
            "entry_fee": detail["tournament"].entry_fee,
            "prize_pool": detail["tournament"].prize_pool,
            "created_by": detail["tournament"].created_by,
            "points_per_win": detail["tournament"].points_per_win or 2,
            "points_per_draw": detail["tournament"].points_per_draw or 1,
            "points_per_no_result": detail["tournament"].points_per_no_result or 0,
            "has_third_place_playoff": detail["tournament"].has_third_place_playoff or False,
        },
        "teams": [{"id": t.id, "name": t.name, "short_name": t.short_name, "color": t.color} for t in detail["teams"]],
        "matches": [serialize_match(m) for m in detail["matches"]],
        "stages": stages,
    }


@router.put("/{tournament_id}")
async def update_tournament(
    tournament_id: int,
    req: UpdateTournamentRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Update tournament fields (status, name, etc.)."""
    from src.database.postgres.repositories.tournament_repository import TournamentRepository
    t = await TournamentRepository.get_by_id(session, tournament_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")
    if t.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the creator can update")
    update = {k: v for k, v in req.model_dump(exclude_unset=True).items()}
    if update:
        await TournamentRepository.update(session, tournament_id, update)
        await session.commit()
    return {"message": "Updated", "updated_fields": list(update.keys())}


@router.post("/{tournament_id}/teams")
async def add_team_to_tournament(
    tournament_id: int,
    req: AddTeamToTournamentRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await TournamentService.add_team(session, tournament_id, req.team_id, user_id=user.id)
    return {"message": "Team added to tournament"}


@router.get("/{tournament_id}/standings")
async def get_tournament_standings(
    tournament_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    # Cache standings (30s TTL) — expensive to recompute
    from src.database.redis.match_cache import MatchCache
    cache_key = f"standings:{tournament_id}"
    try:
        cached = await MatchCache.get_generic(cache_key)
        if cached:
            return cached
    except Exception:
        pass
    standings = await TournamentService.get_standings(session, tournament_id)
    result = {"tournament_id": tournament_id, "standings": standings}
    try:
        # Standings are expensive to compute — cache for 60s
        await MatchCache.set_generic(cache_key, result, ttl=60)
    except Exception:
        pass
    return result


@router.get("/{tournament_id}/leaderboard")
async def get_tournament_leaderboard(
    tournament_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    # Try Redis sorted-set cache first
    cached_batsmen = await LeaderboardCache.get_top_batsmen(tournament_id)
    cached_bowlers = await LeaderboardCache.get_top_bowlers(tournament_id)
    if cached_batsmen is not None and cached_bowlers is not None:
        # Keep the response shape consistent regardless of cache state.
        # The Redis sorted-set cache only stores batsmen + bowlers, so the
        # other two lists come back empty on cache hit (the next miss will
        # repopulate them from SQL). Frontend already handles empty arrays.
        return {
            "tournament_id": tournament_id,
            "top_batsmen": cached_batsmen,
            "top_bowlers": cached_bowlers,
            "top_fielders": [],
            "highest_scores": [],
        }

    # Cache miss -- fall back to SQL
    leaderboard = await TournamentService.get_leaderboard(session, tournament_id)

    # Populate Redis sorted sets for next request
    try:
        for b in leaderboard.get("top_batsmen", []):
            await LeaderboardCache.update_batting_stats(
                tournament_id, b["player_id"], b["player_name"],
                b.get("runs", 0), b.get("balls_faced", 0),
                b.get("fours", 0), b.get("sixes", 0),
            )
        for bw in leaderboard.get("top_bowlers", []):
            await LeaderboardCache.update_bowling_stats(
                tournament_id, bw["player_id"], bw["player_name"],
                bw.get("wickets", 0), bw.get("runs_conceded", 0),
                bw.get("overs", 0),
            )
    except Exception as e:
        logger.warning(f"Leaderboard cache population failed for tournament {tournament_id}: {e}")

    return {"tournament_id": tournament_id, **leaderboard}


@router.delete("/{tournament_id}/teams/{team_id}")
async def remove_team_from_tournament(
    tournament_id: int,
    team_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    return await TournamentService.remove_team(session, tournament_id, team_id, user_id=user.id)


# ═══════════════════════════════════════
# Stage Management Endpoints
# ═══════════════════════════════════════

@router.post("/{tournament_id}/stages")
async def setup_stages(
    tournament_id: int,
    req: SetupStagesRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    stages_config = [{"name": s.name, "qualification_rule": s.qualification_rule} for s in req.stages]
    stages = await TournamentStageService.setup_stages(session, tournament_id, stages_config)
    # Drop the cached tournament detail so the new stage is visible immediately
    from src.utils.cache import invalidate as _invalidate_cache
    await _invalidate_cache(f"tournament:{tournament_id}")
    return {"stages": [{"id": s.id, "stage_name": s.stage_name, "stage_order": s.stage_order} for s in stages]}


@router.get("/{tournament_id}/qualified-teams")
async def get_qualified_teams(
    tournament_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Get teams that qualified from completed stages."""
    stages = await TournamentStageService.get_stages_with_details(session, tournament_id)
    qualified = []
    for s in stages:
        for g in s.get("groups", []):
            for t in g.get("teams", []):
                if t.get("qualification_status") == "qualified":
                    qualified.append({
                        **t,
                        "stage": s["stage_name"],
                        "group": g["group_name"],
                    })
    return qualified


@router.get("/{tournament_id}/stages")
async def get_stages(
    tournament_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    return await TournamentStageService.get_stages_with_details(session, tournament_id)


@router.post("/{tournament_id}/stages/{stage_id}/groups")
async def setup_groups(
    tournament_id: int,
    stage_id: int,
    req: SetupGroupsRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    groups_config = [{"name": g.name, "team_ids": g.team_ids} for g in req.groups]
    groups = await TournamentStageService.setup_groups(session, tournament_id, stage_id, groups_config)
    from src.utils.cache import invalidate as _invalidate_cache
    await _invalidate_cache(f"tournament:{tournament_id}")
    return {"groups": [{"id": g.id, "group_name": g.group_name} for g in groups]}


class SwapMatchTeamsRequest(BaseModel):
    """Swap teams between two knockout matches (e.g., rearrange SF bracket)."""
    match_a_id: int
    match_b_id: int
    # swap_type: "swap_a" swaps team_a of both matches, "swap_b" swaps team_b, "cross" swaps A's team_a with B's team_b
    swap_type: str = "cross"  # "swap_a", "swap_b", "cross"


@router.post("/{tournament_id}/stages/{stage_id}/swap-bracket")
async def swap_bracket_teams(
    tournament_id: int,
    stage_id: int,
    req: SwapMatchTeamsRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Swap teams between two knockout matches to rearrange the bracket.
    Only works for upcoming matches (not live/completed).
    Creator only."""
    from src.database.postgres.repositories.tournament_repository import TournamentRepository
    t = await TournamentRepository.get_by_id(session, tournament_id)
    if not t or t.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only tournament creator can swap bracket")

    match_a = await MatchRepository.get_by_id(session, req.match_a_id)
    match_b = await MatchRepository.get_by_id(session, req.match_b_id)

    if not match_a or not match_b:
        raise HTTPException(status_code=404, detail="Match not found")
    if match_a.stage_id != stage_id or match_b.stage_id != stage_id:
        raise HTTPException(status_code=400, detail="Both matches must be in the same stage")
    if match_a.tournament_id != tournament_id or match_b.tournament_id != tournament_id:
        raise HTTPException(status_code=400, detail="Matches must belong to this tournament")

    # Only allow swapping upcoming matches
    for m in [match_a, match_b]:
        if m.status not in ("upcoming", "created", "scheduled"):
            raise HTTPException(status_code=400, detail=f"Match {m.id} is {m.status} — can only swap upcoming matches")

    # Perform the swap
    if req.swap_type == "swap_a":
        # Swap team_a of both matches
        match_a.team_a_id, match_b.team_a_id = match_b.team_a_id, match_a.team_a_id
    elif req.swap_type == "swap_b":
        # Swap team_b of both matches
        match_a.team_b_id, match_b.team_b_id = match_b.team_b_id, match_a.team_b_id
    elif req.swap_type == "cross":
        # Swap match A's team_a with match B's team_b (common for bracket rearranging)
        match_a.team_a_id, match_b.team_b_id = match_b.team_b_id, match_a.team_a_id
    else:
        raise HTTPException(status_code=400, detail="swap_type must be swap_a, swap_b, or cross")

    await session.commit()
    from src.utils.cache import invalidate as _invalidate_cache
    await _invalidate_cache(f"tournament:{tournament_id}")
    return {
        "status": "swapped",
        "match_a": {"id": match_a.id, "team_a_id": match_a.team_a_id, "team_b_id": match_a.team_b_id},
        "match_b": {"id": match_b.id, "team_a_id": match_b.team_a_id, "team_b_id": match_b.team_b_id},
    }


@router.post("/{tournament_id}/stages/{stage_id}/generate-matches")
async def generate_matches(
    tournament_id: int,
    stage_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    matches = await TournamentStageService.generate_group_matches(session, tournament_id, stage_id)
    # New fixtures are visible — kill the cached tournament detail.
    from src.utils.cache import invalidate as _invalidate_cache
    await _invalidate_cache(f"tournament:{tournament_id}")
    return {"matches_created": len(matches)}


@router.get("/{tournament_id}/stages/{stage_id}/standings")
async def get_stage_standings(
    tournament_id: int,
    stage_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    standings = await TournamentStageService.get_stage_standings(session, stage_id)
    return {"stage_id": stage_id, "groups": standings}


@router.post("/{tournament_id}/stages/{stage_id}/schedule-matches")
async def schedule_matches(
    tournament_id: int,
    stage_id: int,
    req: ScheduleMatchesRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Bulk update match dates/times for matches in a stage."""
    updated = []
    for item in req.schedule:
        match = await MatchRepository.get_by_id(session, item.match_id)
        if not match:
            raise HTTPException(status_code=404, detail=f"Match {item.match_id} not found")
        if match.tournament_id != tournament_id or match.stage_id != stage_id:
            raise HTTPException(
                status_code=400,
                detail=f"Match {item.match_id} does not belong to this tournament/stage",
            )
        update_data = {}
        if item.match_date is not None:
            update_data["match_date"] = item.match_date
        if item.time_slot is not None:
            update_data["time_slot"] = item.time_slot
        if update_data:
            await MatchRepository.update(session, item.match_id, update_data)
            updated.append(item.match_id)
    await session.commit()
    return {"updated_matches": updated, "count": len(updated)}


@router.put("/{tournament_id}/stages/{stage_id}/qualification")
async def update_qualification_rule(
    tournament_id: int,
    stage_id: int,
    req: QualificationRuleRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Update the qualification rule (top_n) for a stage."""
    top_n = req.top_n
    stage = await TournamentStageRepository.get_stage_by_id(session, stage_id)
    if not stage or stage.tournament_id != tournament_id:
        raise HTTPException(status_code=404, detail="Stage not found")
    rule = stage.qualification_rule or {}
    rule["top_n"] = top_n
    rule["from"] = "each_group"
    await TournamentStageRepository.update_stage(session, stage_id, {"qualification_rule": rule})
    await session.commit()
    return {"stage_id": stage_id, "qualification_rule": rule}


@router.delete("/{tournament_id}/stages/{stage_id}")
async def delete_stage(
    tournament_id: int,
    stage_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Delete a stage and all its matches, groups, and team assignments."""
    from src.database.postgres.repositories.tournament_repository import TournamentRepository
    t = await TournamentRepository.get_by_id(session, tournament_id)
    if not t or t.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only tournament creator can reset")

    stage = await TournamentStageRepository.get_stage_by_id(session, stage_id)
    if not stage or stage.tournament_id != tournament_id:
        raise HTTPException(status_code=404, detail="Stage not found")

    from sqlalchemy import delete, text
    # Delete matches in this stage (cascades to deliveries, scorecards, etc.)
    await session.execute(text(
        "DELETE FROM matches WHERE stage_id = :sid AND tournament_id = :tid"
    ), {"sid": stage_id, "tid": tournament_id})
    # Delete group teams
    await session.execute(text(
        "DELETE FROM tournament_group_teams WHERE group_id IN (SELECT id FROM tournament_groups WHERE stage_id = :sid)"
    ), {"sid": stage_id})
    # Delete groups
    await session.execute(text("DELETE FROM tournament_groups WHERE stage_id = :sid"), {"sid": stage_id})
    # Delete stage
    await session.execute(text("DELETE FROM tournament_stages WHERE id = :sid"), {"sid": stage_id})
    # Reset tournament status if needed
    await TournamentRepository.update(session, tournament_id, {"status": "in_progress"})
    await session.commit()
    return {"status": "deleted", "stage_id": stage_id}


@router.post("/{tournament_id}/matches/{match_id}/override")
async def override_match_result(
    tournament_id: int,
    match_id: int,
    req: OverrideMatchRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Override match result: walkover, forfeit, or award match to a team."""
    from src.database.postgres.repositories.tournament_repository import TournamentRepository
    t = await TournamentRepository.get_by_id(session, tournament_id)
    if not t or t.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only tournament creator can override")

    match = await MatchRepository.get_by_id(session, match_id)
    if not match or match.tournament_id != tournament_id:
        raise HTTPException(status_code=404, detail="Match not found")

    winner_id = req.winner_id
    result_type = req.result_type
    reason = req.reason

    if result_type not in ("walkover", "forfeit", "awarded"):
        raise HTTPException(status_code=400, detail="result_type must be walkover, forfeit, or awarded")
    if winner_id not in (match.team_a_id, match.team_b_id):
        raise HTTPException(status_code=400, detail="winner_id must be one of the match teams")

    # Get team names for result summary
    from src.database.postgres.schemas.team_schema import TeamSchema
    winner_team = await session.get(TeamSchema, winner_id)
    winner_name = winner_team.name if winner_team else "Team"
    summary = reason or f"{winner_name} won by {result_type}"

    await MatchRepository.update(session, match_id, {
        "status": "completed", "winner_id": winner_id,
        "result_type": result_type, "result_summary": summary,
    })
    await session.commit()

    # Trigger stage progression
    from src.services.tournament_stage_service import TournamentStageService
    try:
        await TournamentStageService.on_match_completed(session, match_id)
    except Exception as e:
        logger.error(f"Stage progression failed after match override for match {match_id}: {e}")

    return {"status": "overridden", "winner_id": winner_id, "result_type": result_type, "result_summary": summary}


@router.delete("/{tournament_id}/matches/{match_id}")
async def delete_match(
    tournament_id: int,
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Delete a specific match and all its scoring data."""
    from src.database.postgres.repositories.tournament_repository import TournamentRepository
    t = await TournamentRepository.get_by_id(session, tournament_id)
    if not t or t.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only tournament creator can delete matches")

    match = await MatchRepository.get_by_id(session, match_id)
    if not match or match.tournament_id != tournament_id:
        raise HTTPException(status_code=404, detail="Match not found in this tournament")

    from sqlalchemy import text
    # Delete all scoring data (deliveries, scorecards, etc.)
    await session.execute(text("DELETE FROM deliveries WHERE innings_id IN (SELECT id FROM innings WHERE match_id = :mid)"), {"mid": match_id})
    await session.execute(text("DELETE FROM batting_scorecards WHERE innings_id IN (SELECT id FROM innings WHERE match_id = :mid)"), {"mid": match_id})
    await session.execute(text("DELETE FROM bowling_scorecards WHERE innings_id IN (SELECT id FROM innings WHERE match_id = :mid)"), {"mid": match_id})
    await session.execute(text("DELETE FROM fall_of_wickets WHERE innings_id IN (SELECT id FROM innings WHERE match_id = :mid)"), {"mid": match_id})
    await session.execute(text("DELETE FROM partnerships WHERE innings_id IN (SELECT id FROM innings WHERE match_id = :mid)"), {"mid": match_id})
    await session.execute(text("DELETE FROM overs WHERE innings_id IN (SELECT id FROM innings WHERE match_id = :mid)"), {"mid": match_id})
    await session.execute(text("DELETE FROM match_events WHERE match_id = :mid"), {"mid": match_id})
    await session.execute(text("DELETE FROM match_squads WHERE match_id = :mid"), {"mid": match_id})
    await session.execute(text("DELETE FROM innings WHERE match_id = :mid"), {"mid": match_id})
    await session.execute(text("DELETE FROM matches WHERE id = :mid"), {"mid": match_id})
    await session.commit()
    return {"status": "deleted", "match_id": match_id}


@router.post("/{tournament_id}/stages/{stage_id}/reset")
async def reset_stage(
    tournament_id: int,
    stage_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Reset a stage: delete all matches but keep groups and teams. Sets stage back to in_progress."""
    from src.database.postgres.repositories.tournament_repository import TournamentRepository
    t = await TournamentRepository.get_by_id(session, tournament_id)
    if not t or t.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only tournament creator can reset")

    stage = await TournamentStageRepository.get_stage_by_id(session, stage_id)
    if not stage or stage.tournament_id != tournament_id:
        raise HTTPException(status_code=404, detail="Stage not found")

    from sqlalchemy import text
    # Delete all matches in this stage
    match_ids_result = await session.execute(text("SELECT id FROM matches WHERE stage_id = :sid"), {"sid": stage_id})
    mid_list = [r[0] for r in match_ids_result.all()]
    for mid in mid_list:
        # Delete in FK order (children first)
        for tbl in ["deliveries", "batting_scorecards", "bowling_scorecards", "fall_of_wickets", "partnerships", "overs"]:
            try:
                await session.execute(text(f"DELETE FROM {tbl} WHERE innings_id IN (SELECT id FROM innings WHERE match_id = :mid)"), {"mid": mid})
            except Exception as e:
                logger.warning(f"Failed to delete from {tbl} for match {mid} during stage reset: {e}")
        for tbl in ["match_events", "match_squads", "match_subscriptions"]:
            try:
                await session.execute(text(f"DELETE FROM {tbl} WHERE match_id = :mid"), {"mid": mid})
            except Exception as e:
                logger.warning(f"Failed to delete from {tbl} for match {mid} during stage reset: {e}")
        await session.execute(text("DELETE FROM innings WHERE match_id = :mid"), {"mid": mid})
    await session.execute(text("DELETE FROM matches WHERE stage_id = :sid"), {"sid": stage_id})

    # Reset qualification status for teams in this stage
    await session.execute(text(
        "UPDATE tournament_group_teams SET qualification_status = NULL WHERE group_id IN (SELECT id FROM tournament_groups WHERE stage_id = :sid)"
    ), {"sid": stage_id})

    # Reset stage status
    await TournamentStageRepository.update_stage(session, stage_id, {"status": "in_progress"})
    await TournamentRepository.update(session, tournament_id, {"status": "in_progress"})
    await session.commit()
    return {"status": "reset", "stage_id": stage_id}
