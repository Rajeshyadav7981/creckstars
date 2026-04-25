from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user, get_current_user_optional
from src.services.player_service import PlayerService
from src.app.api.routers.models.player_model import CreatePlayerRequest, UpdatePlayerRequest

router = APIRouter(prefix="/api/players", tags=["Players"])


def _player_summary(p) -> dict:
    return {
        "id": p.id, "first_name": p.first_name, "last_name": p.last_name,
        "full_name": p.full_name, "mobile": p.mobile,
        "date_of_birth": str(p.date_of_birth) if p.date_of_birth else None,
        "bio": p.bio, "city": p.city,
        "state_province": p.state_province, "country": p.country,
        "batting_style": p.batting_style, "bowling_style": p.bowling_style,
        "role": p.role, "profile_image": p.profile_image,
    }


@router.post("")
async def create_player(
    req: CreatePlayerRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    if req.user_id:
        player = await PlayerService.get_or_create_for_user(session, req.user_id, user.id)
    else:
        player = await PlayerService.create_player(
            session, user.id, req.first_name, req.last_name, req.mobile,
            req.batting_style, req.bowling_style, req.role, req.profile_image,
            date_of_birth=req.date_of_birth, bio=req.bio,
            city=req.city, state_province=req.state_province, country=req.country,
            is_guest=req.is_guest,
        )
    return {
        "id": player.id,
        "full_name": player.full_name,
        "role": player.role,
        "is_guest": bool(getattr(player, "is_guest", False)),
        "user_id": player.user_id,
    }


@router.get("")
async def list_players(
    search: str = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    players = await PlayerService.get_players(
        session, search=search, created_by=user.id, limit=limit, offset=offset
    )
    return [
        {"id": p.id, "full_name": p.full_name, "role": p.role, "mobile": p.mobile}
        for p in players
    ]


@router.get("/{player_id}")
async def get_player(
    player_id: int,
    session: AsyncSession = Depends(get_async_db),
    _user=Depends(get_current_user_optional),
):
    player = await PlayerService.get_player(session, player_id)
    return _player_summary(player)


@router.put("/{player_id}")
async def update_player(
    player_id: int,
    req: UpdatePlayerRequest,
    session: AsyncSession = Depends(get_async_db),
    _user=Depends(get_current_user),
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
    """Full career stats for a player — Redis-cached, viewer-aware."""
    viewer_id = user.id if user else None
    return await PlayerService.get_full_stats(session, player_id, viewer_id)
