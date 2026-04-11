from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, Integer, case
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user, get_current_user_optional
from src.services.player_service import PlayerService
from src.app.api.routers.models.player_model import CreatePlayerRequest, UpdatePlayerRequest
from src.database.postgres.schemas.batting_scorecard_schema import BattingScorecardSchema
from src.database.postgres.schemas.bowling_scorecard_schema import BowlingScorecardSchema
from src.database.postgres.schemas.innings_schema import InningsSchema
from src.database.postgres.schemas.match_schema import MatchSchema
from src.database.postgres.schemas.team_schema import TeamSchema
from src.database.postgres.schemas.match_squad_schema import MatchSquadSchema

router = APIRouter(prefix="/api/players", tags=["Players"])


@router.post("")
async def create_player(
    req: CreatePlayerRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    # If user_id provided, use get_or_create to link to existing user
    if req.user_id:
        player = await PlayerService.get_or_create_for_user(session, req.user_id, user.id)
    else:
        player = await PlayerService.create_player(
            session, user.id, req.first_name, req.last_name, req.mobile,
            req.batting_style, req.bowling_style, req.role, req.profile_image,
            date_of_birth=req.date_of_birth, bio=req.bio,
            city=req.city, state_province=req.state_province, country=req.country,
        )
    return {"id": player.id, "full_name": player.full_name, "role": player.role}


@router.get("")
async def list_players(
    search: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    players = await PlayerService.get_players(session, search=search, created_by=user.id, limit=limit, offset=offset)
    return [{"id": p.id, "full_name": p.full_name, "role": p.role, "mobile": p.mobile} for p in players]


@router.get("/{player_id}")
async def get_player(
    player_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    player = await PlayerService.get_player(session, player_id)
    return {
        "id": player.id, "first_name": player.first_name, "last_name": player.last_name,
        "full_name": player.full_name, "mobile": player.mobile,
        "date_of_birth": str(player.date_of_birth) if player.date_of_birth else None,
        "bio": player.bio, "city": player.city,
        "state_province": player.state_province, "country": player.country,
        "batting_style": player.batting_style, "bowling_style": player.bowling_style,
        "role": player.role, "profile_image": player.profile_image,
    }


@router.put("/{player_id}")
async def update_player(
    player_id: int,
    req: UpdatePlayerRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    data = req.model_dump(exclude_unset=True)
    player = await PlayerService.update_player(session, player_id, data)
    return {
        "id": player.id, "full_name": player.full_name,
        "date_of_birth": str(player.date_of_birth) if player.date_of_birth else None,
        "bio": player.bio, "city": player.city,
        "state_province": player.state_province, "country": player.country,
    }


@router.get("/{player_id}/stats")
async def get_player_stats(
    player_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Get aggregated career stats for a player across all matches."""
    from src.database.redis.match_cache import MatchCache
    cached = await MatchCache.get_generic(f"player_stats:{player_id}")
    if cached:
        return cached
    player = await PlayerService.get_player(session, player_id)

    # ── QUERY 1: Batting aggregates + 50s/100s in ONE query ──
    bat_result = await session.execute(
        select(
            func.count(BattingScorecardSchema.id).label("innings"),
            func.sum(BattingScorecardSchema.runs).label("runs"),
            func.sum(BattingScorecardSchema.balls_faced).label("balls"),
            func.sum(BattingScorecardSchema.fours).label("fours"),
            func.sum(BattingScorecardSchema.sixes).label("sixes"),
            func.max(BattingScorecardSchema.runs).label("highest"),
            func.sum(case((BattingScorecardSchema.is_out == True, 1), else_=0)).label("outs"),
            func.sum(case((BattingScorecardSchema.runs >= 50, 1), else_=0)).label("fifties_plus"),
            func.sum(case((BattingScorecardSchema.runs >= 100, 1), else_=0)).label("hundreds"),
        ).where(BattingScorecardSchema.player_id == player_id)
    )
    bat_row = bat_result.one()
    bat_innings = bat_row.innings or 0
    bat_runs = bat_row.runs or 0
    bat_balls = bat_row.balls or 0
    bat_outs = bat_row.outs or 0
    hundreds = bat_row.hundreds or 0
    batting = {
        "innings": bat_innings,
        "runs": bat_runs,
        "balls_faced": bat_balls,
        "fours": bat_row.fours or 0,
        "sixes": bat_row.sixes or 0,
        "highest": bat_row.highest or 0,
        "average": round(bat_runs / bat_outs, 2) if bat_outs > 0 else bat_runs,
        "strike_rate": round((bat_runs / bat_balls) * 100, 2) if bat_balls > 0 else 0.0,
        "not_outs": bat_innings - bat_outs,
        "fifties": (bat_row.fifties_plus or 0) - hundreds,  # 50-99 range
        "hundreds": hundreds,
    }

    # ── QUERY 2: Bowling aggregates + best figures in ONE query ──
    bowl_result = await session.execute(
        select(
            func.count(BowlingScorecardSchema.id).label("innings"),
            func.sum(BowlingScorecardSchema.overs_bowled).label("overs"),
            func.sum(BowlingScorecardSchema.maidens).label("maidens"),
            func.sum(BowlingScorecardSchema.runs_conceded).label("runs"),
            func.sum(BowlingScorecardSchema.wickets).label("wickets"),
            func.sum(BowlingScorecardSchema.wides).label("wides"),
            func.sum(BowlingScorecardSchema.no_balls).label("no_balls"),
            func.sum(BowlingScorecardSchema.dot_balls).label("dots"),
        ).where(BowlingScorecardSchema.player_id == player_id)
    )
    bowl_row = bowl_result.one()
    bowl_innings = bowl_row.innings or 0
    bowl_overs = float(bowl_row.overs or 0)
    bowl_runs = bowl_row.runs or 0
    bowl_wickets = bowl_row.wickets or 0

    # Best bowling (separate lightweight query — single row)
    best_result = await session.execute(
        select(BowlingScorecardSchema.wickets, BowlingScorecardSchema.runs_conceded)
        .where(BowlingScorecardSchema.player_id == player_id, BowlingScorecardSchema.wickets > 0)
        .order_by(BowlingScorecardSchema.wickets.desc(), BowlingScorecardSchema.runs_conceded.asc())
        .limit(1)
    )
    best_row = best_result.first()
    bowling = {
        "innings": bowl_innings,
        "overs": bowl_overs,
        "maidens": bowl_row.maidens or 0,
        "runs_conceded": bowl_runs,
        "wickets": bowl_wickets,
        "wides": bowl_row.wides or 0,
        "no_balls": bowl_row.no_balls or 0,
        "dot_balls": bowl_row.dots or 0,
        "economy": round(bowl_runs / bowl_overs, 2) if bowl_overs > 0 else 0.0,
        "average": round(bowl_runs / bowl_wickets, 2) if bowl_wickets > 0 else 0.0,
        "best": f"{best_row.wickets}/{best_row.runs_conceded}" if best_row and best_row.wickets else "0/0",
    }

    # ── QUERY 3: Match count + teams (2 lightweight queries) ──
    match_teams_result = await session.execute(
        select(
            func.count(func.distinct(MatchSquadSchema.match_id)).label("matches"),
        ).where(MatchSquadSchema.player_id == player_id)
    )
    matches_played = match_teams_result.scalar() or 0

    teams_result = await session.execute(
        select(TeamSchema.id, TeamSchema.name, TeamSchema.short_name, TeamSchema.color)
        .join(MatchSquadSchema, MatchSquadSchema.team_id == TeamSchema.id)
        .where(MatchSquadSchema.player_id == player_id)
        .group_by(TeamSchema.id)
    )
    teams = [{"id": t.id, "name": t.name, "short_name": t.short_name, "color": t.color}
             for t in teams_result.all()]

    # ── QUERY 4: Recent batting innings (last 10) with match context ──
    recent_result = await session.execute(
        select(
            BattingScorecardSchema.runs,
            BattingScorecardSchema.balls_faced,
            BattingScorecardSchema.fours,
            BattingScorecardSchema.sixes,
            BattingScorecardSchema.is_out,
            BattingScorecardSchema.how_out,
            InningsSchema.match_id,
            InningsSchema.innings_number,
            InningsSchema.bowling_team_id,
            MatchSchema.overs.label("match_overs"),
            MatchSchema.match_date,
            MatchSchema.winner_id,
            MatchSchema.status.label("match_status"),
        )
        .join(InningsSchema, BattingScorecardSchema.innings_id == InningsSchema.id)
        .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
        .where(
            BattingScorecardSchema.player_id == player_id,
            MatchSchema.status == "completed",
        )
        .order_by(BattingScorecardSchema.id.desc())
        .limit(10)
    )
    # Build opponent name map
    from sqlalchemy.orm import load_only as _lo
    recent_rows = recent_result.all()
    opp_team_ids = set(r.bowling_team_id for r in recent_rows if r.bowling_team_id)
    opp_names = {}
    if opp_team_ids:
        opp_result = await session.execute(
            select(TeamSchema).options(_lo(TeamSchema.id, TeamSchema.name, TeamSchema.short_name))
            .where(TeamSchema.id.in_(opp_team_ids))
        )
        for t in opp_result.scalars().all():
            opp_names[t.id] = t.short_name or t.name

    # Determine win/loss: bowling_team_id is the opponent, so if winner != opponent → player won
    recent_innings = []
    for r in recent_rows:
        result_char = None
        if r.winner_id:
            if r.winner_id != r.bowling_team_id:
                result_char = "W"
            else:
                result_char = "L"

        recent_innings.append({
            "match_id": r.match_id, "innings_number": r.innings_number,
            "runs": r.runs, "balls_faced": r.balls_faced,
            "fours": r.fours, "sixes": r.sixes,
            "is_out": r.is_out, "how_out": r.how_out,
            "match_format": f"T{r.match_overs}" if r.match_overs else None,
            "match_date": str(r.match_date) if r.match_date else None,
            "opponent_team": opp_names.get(r.bowling_team_id),
            "result": result_char,
        })

    # ── QUERY 5: Recent bowling spells (last 10) with match context ──
    recent_bowl_result = await session.execute(
        select(
            BowlingScorecardSchema.overs_bowled,
            BowlingScorecardSchema.runs_conceded,
            BowlingScorecardSchema.wickets,
            BowlingScorecardSchema.maidens,
            BowlingScorecardSchema.economy_rate,
            BowlingScorecardSchema.dot_balls,
            BowlingScorecardSchema.wides,
            BowlingScorecardSchema.no_balls,
            InningsSchema.match_id,
            InningsSchema.innings_number,
            InningsSchema.batting_team_id,
            MatchSchema.overs.label("match_overs"),
            MatchSchema.match_date,
        )
        .join(InningsSchema, BowlingScorecardSchema.innings_id == InningsSchema.id)
        .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
        .where(
            BowlingScorecardSchema.player_id == player_id,
            MatchSchema.status == "completed",
        )
        .order_by(BowlingScorecardSchema.id.desc())
        .limit(10)
    )
    bowl_rows = recent_bowl_result.all()
    # Get opponent names for bowling (batting_team_id = opponent of bowler)
    bowl_opp_ids = set(r.batting_team_id for r in bowl_rows if r.batting_team_id)
    bowl_opp_names = {}
    if bowl_opp_ids - opp_team_ids:  # Only fetch IDs we don't already have
        new_ids = bowl_opp_ids - set(opp_names.keys())
        if new_ids:
            opp_result2 = await session.execute(
                select(TeamSchema).options(_lo(TeamSchema.id, TeamSchema.name, TeamSchema.short_name))
                .where(TeamSchema.id.in_(new_ids))
            )
            for t in opp_result2.scalars().all():
                opp_names[t.id] = t.short_name or t.name

    recent_bowling = [
        {
            "match_id": r.match_id, "innings_number": r.innings_number,
            "overs": float(r.overs_bowled or 0), "runs": r.runs_conceded or 0,
            "wickets": r.wickets or 0, "maidens": r.maidens or 0,
            "economy": float(r.economy_rate or 0), "dots": r.dot_balls or 0,
            "wides": r.wides or 0, "no_balls": r.no_balls or 0,
            "match_format": f"T{r.match_overs}" if r.match_overs else None,
            "match_date": str(r.match_date) if r.match_date else None,
            "opponent_team": opp_names.get(r.batting_team_id),
        }
        for r in bowl_rows
    ]

    # ── Format-wise stats (grouped by match overs) ──
    format_stats = {}
    format_query = await session.execute(
        select(
            MatchSchema.overs,
            func.count(func.distinct(MatchSquadSchema.match_id)).label("matches"),
            func.sum(BattingScorecardSchema.runs).label("bat_runs"),
            func.sum(BattingScorecardSchema.balls_faced).label("bat_balls"),
            func.sum(BattingScorecardSchema.fours).label("bat_fours"),
            func.sum(BattingScorecardSchema.sixes).label("bat_sixes"),
            func.max(BattingScorecardSchema.runs).label("bat_highest"),
            func.count(BattingScorecardSchema.id).label("bat_innings"),
            func.sum(case((BattingScorecardSchema.is_out == True, 1), else_=0)).label("bat_outs"),
        )
        .select_from(BattingScorecardSchema)
        .join(InningsSchema, BattingScorecardSchema.innings_id == InningsSchema.id)
        .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
        .join(MatchSquadSchema, (MatchSquadSchema.match_id == MatchSchema.id) & (MatchSquadSchema.player_id == player_id))
        .where(BattingScorecardSchema.player_id == player_id)
        .group_by(MatchSchema.overs)
    )
    for row in format_query.all():
        overs = row.overs or 20
        label = f"T{overs}"
        runs = row.bat_runs or 0
        balls = row.bat_balls or 0
        outs = row.bat_outs or 0
        innings = row.bat_innings or 0
        format_stats[label] = {
            "matches": row.matches or 0,
            "batting": {
                "innings": innings,
                "runs": runs,
                "balls_faced": balls,
                "fours": row.bat_fours or 0,
                "sixes": row.bat_sixes or 0,
                "highest": row.bat_highest or 0,
                "average": round(runs / outs, 2) if outs > 0 else float(runs),
                "strike_rate": round((runs / balls) * 100, 2) if balls > 0 else 0.0,
            },
        }

    # Add bowling stats per format
    bowl_format_query = await session.execute(
        select(
            MatchSchema.overs,
            func.sum(BowlingScorecardSchema.overs_bowled).label("bowl_overs"),
            func.sum(BowlingScorecardSchema.runs_conceded).label("bowl_runs"),
            func.sum(BowlingScorecardSchema.wickets).label("bowl_wickets"),
            func.sum(BowlingScorecardSchema.maidens).label("bowl_maidens"),
            func.count(BowlingScorecardSchema.id).label("bowl_innings"),
        )
        .select_from(BowlingScorecardSchema)
        .join(InningsSchema, BowlingScorecardSchema.innings_id == InningsSchema.id)
        .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
        .where(BowlingScorecardSchema.player_id == player_id)
        .group_by(MatchSchema.overs)
    )
    for row in bowl_format_query.all():
        overs = row.overs or 20
        label = f"T{overs}"
        if label not in format_stats:
            format_stats[label] = {"matches": 0, "batting": {}}
        bowl_overs = float(row.bowl_overs or 0)
        bowl_runs = row.bowl_runs or 0
        bowl_wickets = row.bowl_wickets or 0
        format_stats[label]["bowling"] = {
            "innings": row.bowl_innings or 0,
            "overs": bowl_overs,
            "wickets": bowl_wickets,
            "runs_conceded": bowl_runs,
            "maidens": row.bowl_maidens or 0,
            "economy": round(bowl_runs / bowl_overs, 2) if bowl_overs > 0 else 0.0,
            "average": round(bowl_runs / bowl_wickets, 2) if bowl_wickets > 0 else 0.0,
        }

    result = {
        "player": {
            "id": player.id,
            "first_name": player.first_name,
            "last_name": player.last_name,
            "full_name": player.full_name,
            "mobile": player.mobile,
            "date_of_birth": str(player.date_of_birth) if player.date_of_birth else None,
            "bio": player.bio,
            "city": player.city,
            "state_province": player.state_province,
            "country": player.country,
            "batting_style": player.batting_style,
            "bowling_style": player.bowling_style,
            "role": player.role,
            "profile_image": player.profile_image,
        },
        "matches_played": matches_played,
        "teams": teams,
        "batting": batting,
        "bowling": bowling,
        "format_stats": format_stats,
        "recent_innings": recent_innings,
        "recent_bowling": recent_bowling,
    }
    await MatchCache.set_generic(f"player_stats:{player_id}", result, ttl=600)
    return result
