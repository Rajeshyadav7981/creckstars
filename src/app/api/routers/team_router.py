from fastapi import APIRouter, Depends, Query
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user, get_current_user_optional
from src.services.team_service import TeamService
from fastapi import HTTPException
from src.app.api.routers.models.team_model import CreateTeamRequest, AddPlayerToTeamRequest, UpdatePlayerRoleRequest
from src.app.api.rate_limiter import limiter
from src.app.api.config import RATE_LIMITS

router = APIRouter(prefix="/api/teams", tags=["Teams"])


@router.post("")
@limiter.limit(RATE_LIMITS["create_team"])
async def create_team(
    request: Request,
    req: CreateTeamRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    team = await TeamService.create_team(
        session, req.name, user.id, req.short_name, req.logo_url, req.color, req.home_ground,
        city=req.city, latitude=req.latitude, longitude=req.longitude,
    )
    from src.app.api.routers.user_router import invalidate_user_stats
    await invalidate_user_stats(user.id)
    return {"id": team.id, "team_code": team.team_code, "name": team.name, "short_name": team.short_name, "color": team.color, "city": team.city, "created_by": team.created_by}


@router.get("")
async def list_teams(
    search: str = Query(None, description="Search teams by name"),
    code: str = Query(None, description="Exact team code lookup (fast)"),
    created_by: int = Query(None, description="Filter by creator user ID"),
    lat: float = Query(None, description="Latitude for nearby sorting"),
    lng: float = Query(None, description="Longitude for nearby sorting"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    teams = await TeamService.get_teams(session, created_by=created_by, search=search, code=code, lat=lat, lng=lng, limit=limit, offset=offset)
    return [{"id": t.id, "team_code": t.team_code, "name": t.name, "short_name": t.short_name, "color": t.color, "city": t.city, "created_by": t.created_by} for t in teams]


@router.get("/{team_id}")
async def get_team(
    team_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    return await TeamService.get_team_detail(session, team_id)


@router.post("/{team_id}/players")
async def add_player_to_team(
    team_id: int,
    req: AddPlayerToTeamRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    await TeamService.add_player(session, team_id, req.player_id, req.jersey_number,
                                req.is_captain, req.is_vice_captain, req.is_wicket_keeper, user_id=user.id)
    # Invalidate stats for the player's linked user (teams count / played tournaments changed)
    from src.app.api.routers.user_router import invalidate_user_stats
    from src.database.postgres.schemas.player_schema import PlayerSchema as _PS
    res = await session.execute(select(_PS.user_id).where(_PS.id == req.player_id))
    uid = res.scalar_one_or_none()
    if uid:
        await invalidate_user_stats(uid)
    return {"message": "Player added to team"}


@router.put("/{team_id}/players/{player_id}")
async def update_player_role(
    team_id: int,
    player_id: int,
    req: UpdatePlayerRoleRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Update captain/vice-captain/WK status. Previous captain is automatically unset."""
    updates = req.model_dump(exclude_unset=True)
    result = await TeamService.update_player_role(session, team_id, player_id, updates, user_id=user.id)
    if not result:
        raise HTTPException(status_code=404, detail="Player not in team")
    return {"message": "Player role updated"}


@router.delete("/{team_id}/players/{player_id}")
async def remove_player_from_team(
    team_id: int,
    player_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    return await TeamService.remove_player(session, team_id, player_id, user_id=user.id)
