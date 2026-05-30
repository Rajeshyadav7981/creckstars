from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.postgres.db import get_async_db
from src.database.postgres.repositories.favorite_repository import FavoriteRepository
from src.database.postgres.repositories.match_repository import MatchRepository
from src.database.postgres.repositories.tournament_repository import TournamentRepository
from src.database.postgres.schemas.match_schema import MatchSchema
from src.database.postgres.schemas.team_schema import TeamSchema
from src.database.postgres.schemas.tournament_schema import TournamentSchema
from src.database.postgres.schemas.venue_schema import VenueSchema
from src.database.postgres.schemas.innings_schema import InningsSchema
from src.database.postgres.schemas.user_favorite_match_schema import UserFavoriteMatchSchema
from src.database.postgres.schemas.user_favorite_tournament_schema import UserFavoriteTournamentSchema
from src.utils.security import get_current_user
from src.app.api.rate_limiter import limiter
from src.app.api.config import RATE_LIMITS


router = APIRouter(prefix="/api/favorites", tags=["Favorites"])


@router.get("/ids")
@limiter.limit(RATE_LIMITS["list_matches"])
async def get_favorite_ids(
    request: Request,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    return await FavoriteRepository.all_ids(session, user.id)


@router.post("/matches/{match_id}")
@limiter.limit(RATE_LIMITS["list_matches"])
async def add_favorite_match(
    request: Request,
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    match = await MatchRepository.get_by_id(session, match_id)
    if not match:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    added = await FavoriteRepository.add_match(session, user.id, match_id)
    return {"favorited": True, "added": added}


@router.delete("/matches/{match_id}")
@limiter.limit(RATE_LIMITS["list_matches"])
async def remove_favorite_match(
    request: Request,
    match_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    removed = await FavoriteRepository.remove_match(session, user.id, match_id)
    return {"favorited": False, "removed": removed}


@router.get("/matches")
@limiter.limit(RATE_LIMITS["list_matches"])
async def list_favorite_matches(
    request: Request,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    ids = await FavoriteRepository.list_match_ids(session, user.id)
    if not ids:
        return []

    fav_at_res = await session.execute(
        select(UserFavoriteMatchSchema.match_id, UserFavoriteMatchSchema.created_at)
        .where(UserFavoriteMatchSchema.user_id == user.id, UserFavoriteMatchSchema.match_id.in_(ids))
    )
    favorited_at_by_id = {mid: ts.isoformat() if ts else None for mid, ts in fav_at_res.all()}

    m_res = await session.execute(select(MatchSchema).where(MatchSchema.id.in_(ids)))
    matches = {m.id: m for m in m_res.scalars().all()}

    tournament_ids = {m.tournament_id for m in matches.values() if m.tournament_id}
    tournament_names = {}
    if tournament_ids:
        t_res = await session.execute(
            select(TournamentSchema.id, TournamentSchema.name).where(TournamentSchema.id.in_(tournament_ids))
        )
        tournament_names = {tid: tname for tid, tname in t_res.all()}

    from src.database.postgres.schemas.tournament_stage_schema import TournamentStageSchema
    stage_ids = {m.stage_id for m in matches.values() if m.stage_id}
    stage_info = {}
    if stage_ids:
        s_res = await session.execute(
            select(TournamentStageSchema.id, TournamentStageSchema.stage_name, TournamentStageSchema.stage_label)
            .where(TournamentStageSchema.id.in_(stage_ids))
        )
        stage_info = {sid: {"name": sname, "label": slabel} for sid, sname, slabel in s_res.all()}

    team_ids = set()
    for m in matches.values():
        if m.team_a_id: team_ids.add(m.team_a_id)
        if m.team_b_id: team_ids.add(m.team_b_id)
    team_names = {}
    if team_ids:
        t_res = await session.execute(select(TeamSchema.id, TeamSchema.name).where(TeamSchema.id.in_(team_ids)))
        team_names = {tid: tname for tid, tname in t_res.all()}

    venue_ids = {m.venue_id for m in matches.values() if m.venue_id}
    venue_names = {}
    if venue_ids:
        v_res = await session.execute(select(VenueSchema.id, VenueSchema.name).where(VenueSchema.id.in_(venue_ids)))
        venue_names = {vid: vname for vid, vname in v_res.all()}

    scores = {}
    inn_res = await session.execute(
        select(
            InningsSchema.match_id, InningsSchema.batting_team_id,
            InningsSchema.total_runs, InningsSchema.total_wickets, InningsSchema.total_overs,
        ).where(InningsSchema.match_id.in_(ids))
    )
    for row in inn_res.all():
        scores.setdefault(row.match_id, {})[row.batting_team_id] = {
            "runs": row.total_runs, "wickets": row.total_wickets, "overs": row.total_overs,
        }

    out = []
    for mid in ids:
        m = matches.get(mid)
        if not m:
            continue
        ms = scores.get(m.id, {})
        sa = ms.get(m.team_a_id, {})
        sb = ms.get(m.team_b_id, {})
        out.append({
            "id": m.id, "match_code": m.match_code, "name": m.name, "status": m.status,
            "team_a_id": m.team_a_id, "team_b_id": m.team_b_id,
            "team_a_name": team_names.get(m.team_a_id), "team_b_name": team_names.get(m.team_b_id),
            "team_a_runs": sa.get("runs"), "team_a_wickets": sa.get("wickets"), "team_a_overs": sa.get("overs"),
            "team_b_runs": sb.get("runs"), "team_b_wickets": sb.get("wickets"), "team_b_overs": sb.get("overs"),
            "overs": m.overs, "tournament_id": m.tournament_id,
            "stage_id": m.stage_id, "group_id": m.group_id, "match_number": m.match_number,
            "match_date": str(m.match_date) if m.match_date else None,
            "venue_name": venue_names.get(m.venue_id),
            "result_summary": m.result_summary, "winner_id": m.winner_id,
            "toss_winner_id": m.toss_winner_id, "toss_decision": m.toss_decision,
            "match_type": m.match_type,
            "tournament_name": tournament_names.get(m.tournament_id),
            "stage_name": stage_info.get(m.stage_id, {}).get("name"),
            "stage_label": stage_info.get(m.stage_id, {}).get("label"),
            "favorited_at": favorited_at_by_id.get(m.id),
        })
    return out


@router.post("/tournaments/{tournament_id}")
@limiter.limit(RATE_LIMITS["list_tournaments"])
async def add_favorite_tournament(
    request: Request,
    tournament_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    tournament = await TournamentRepository.get_by_id(session, tournament_id)
    if not tournament:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")
    added = await FavoriteRepository.add_tournament(session, user.id, tournament_id)
    return {"favorited": True, "added": added}


@router.delete("/tournaments/{tournament_id}")
@limiter.limit(RATE_LIMITS["list_tournaments"])
async def remove_favorite_tournament(
    request: Request,
    tournament_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    removed = await FavoriteRepository.remove_tournament(session, user.id, tournament_id)
    return {"favorited": False, "removed": removed}


@router.get("/tournaments")
@limiter.limit(RATE_LIMITS["list_tournaments"])
async def list_favorite_tournaments(
    request: Request,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    ids = await FavoriteRepository.list_tournament_ids(session, user.id)
    if not ids:
        return []

    fav_at_res = await session.execute(
        select(UserFavoriteTournamentSchema.tournament_id, UserFavoriteTournamentSchema.created_at)
        .where(UserFavoriteTournamentSchema.user_id == user.id, UserFavoriteTournamentSchema.tournament_id.in_(ids))
    )
    favorited_at_by_id = {tid: ts.isoformat() if ts else None for tid, ts in fav_at_res.all()}

    t_res = await session.execute(select(TournamentSchema).where(TournamentSchema.id.in_(ids)))
    tournaments = {t.id: t for t in t_res.scalars().all()}

    from sqlalchemy import func as _func, case as _case
    from src.database.postgres.schemas.tournament_stage_schema import TournamentStageSchema
    stage_counts = {}
    matches_total = {}
    matches_completed = {}
    sres = await session.execute(
        select(TournamentStageSchema.tournament_id, _func.count(TournamentStageSchema.id))
        .where(TournamentStageSchema.tournament_id.in_(ids))
        .group_by(TournamentStageSchema.tournament_id)
    )
    for tid, cnt in sres.all():
        stage_counts[tid] = cnt or 0
    mres = await session.execute(
        select(
            MatchSchema.tournament_id,
            _func.count(MatchSchema.id).label("total"),
            _func.sum(_case((MatchSchema.status == "completed", 1), else_=0)).label("completed"),
        )
        .where(MatchSchema.tournament_id.in_(ids))
        .group_by(MatchSchema.tournament_id)
    )
    for tid, total, completed in mres.all():
        matches_total[tid] = total or 0
        matches_completed[tid] = int(completed or 0)

    out = []
    for tid in ids:
        t = tournaments.get(tid)
        if not t:
            continue
        out.append({
            "id": t.id, "tournament_code": t.tournament_code, "name": t.name, "status": t.status,
            "tournament_type": t.tournament_type, "overs_per_match": t.overs_per_match,
            "ball_type": t.ball_type,
            "start_date": str(t.start_date) if t.start_date else None,
            "end_date": str(t.end_date) if t.end_date else None,
            "organizer_name": t.organizer_name, "location": t.location,
            "entry_fee": t.entry_fee, "prize_pool": t.prize_pool,
            "stages_count": stage_counts.get(t.id, 0),
            "matches_total": matches_total.get(t.id, 0),
            "matches_completed": matches_completed.get(t.id, 0),
            "favorited_at": favorited_at_by_id.get(t.id),
        })
    return out
