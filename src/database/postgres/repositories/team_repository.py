from sqlalchemy import select, case, func
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.team_schema import TeamSchema
from src.database.postgres.schemas.team_player_schema import TeamPlayerSchema
from src.database.postgres.schemas.player_schema import PlayerSchema


class TeamRepository:

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> TeamSchema:
        team = TeamSchema(**data)
        session.add(team)
        await session.commit()
        await session.refresh(team)
        return team

    @staticmethod
    async def get_by_id(session: AsyncSession, team_id: int) -> TeamSchema | None:
        result = await session.execute(select(TeamSchema).where(TeamSchema.id == team_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_code(session: AsyncSession, code: str) -> TeamSchema | None:
        result = await session.execute(select(TeamSchema).where(TeamSchema.team_code == code))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(
        session: AsyncSession, created_by: int = None,
        search: str = None, code: str = None,
        lat: float = None, lng: float = None,
        limit: int = 50, offset: int = 0,
    ) -> list:
        from sqlalchemy.orm import load_only
        query = select(TeamSchema).options(load_only(
            TeamSchema.id, TeamSchema.name, TeamSchema.short_name,
            TeamSchema.team_code, TeamSchema.color, TeamSchema.logo_url,
            TeamSchema.city, TeamSchema.created_by, TeamSchema.created_at,
            TeamSchema.latitude, TeamSchema.longitude,
        ))
        if created_by:
            query = query.where(TeamSchema.created_by == created_by)
        if code:
            # Exact match on indexed column — fast even with millions of rows
            query = query.where(TeamSchema.team_code == code.strip().upper())
        elif search:
            query = query.where(TeamSchema.name.ilike(f"%{search}%"))
        # Sort by distance if lat/lng provided (nearby teams first)
        if lat is not None and lng is not None:
            # Teams with coordinates: sort by approximate distance
            # Teams without coordinates: pushed to the end
            has_coords = TeamSchema.latitude.isnot(None)
            # Simplified distance using Euclidean approx (good enough for sorting)
            dist = (
                (TeamSchema.latitude - lat) * (TeamSchema.latitude - lat) +
                (TeamSchema.longitude - lng) * (TeamSchema.longitude - lng)
            )
            query = query.order_by(
                case((has_coords, 0), else_=1),  # teams with coords first
                case((has_coords, dist), else_=999999),
            )
        else:
            query = query.order_by(TeamSchema.created_at.desc())
        query = query.limit(limit).offset(offset)
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def add_player(session: AsyncSession, data: dict) -> TeamPlayerSchema:
        tp = TeamPlayerSchema(**data)
        session.add(tp)
        await session.commit()
        await session.refresh(tp)
        return tp

    @staticmethod
    async def get_team_players(session: AsyncSession, team_id: int) -> list:
        result = await session.execute(
            select(PlayerSchema, TeamPlayerSchema)
            .join(TeamPlayerSchema, PlayerSchema.id == TeamPlayerSchema.player_id)
            .where(TeamPlayerSchema.team_id == team_id)
        )
        return result.all()

    @staticmethod
    async def unset_role(session: AsyncSession, team_id: int, role_field: str):
        """Unset a boolean role (is_captain, is_vice_captain) for all players in a team."""
        from sqlalchemy import update
        await session.execute(
            update(TeamPlayerSchema)
            .where(TeamPlayerSchema.team_id == team_id, getattr(TeamPlayerSchema, role_field) == True)
            .values({role_field: False})
        )
        await session.flush()

    @staticmethod
    async def update_player(session: AsyncSession, team_id: int, player_id: int, updates: dict):
        """Update a team player's role/jersey."""
        result = await session.execute(
            select(TeamPlayerSchema).where(
                TeamPlayerSchema.team_id == team_id,
                TeamPlayerSchema.player_id == player_id,
            )
        )
        tp = result.scalar_one_or_none()
        if not tp:
            return None
        for k, v in updates.items():
            if v is not None and hasattr(tp, k):
                setattr(tp, k, v)
        await session.commit()
        return tp

    @staticmethod
    async def remove_player(session: AsyncSession, team_id: int, player_id: int) -> bool:
        result = await session.execute(
            select(TeamPlayerSchema)
            .where(TeamPlayerSchema.team_id == team_id, TeamPlayerSchema.player_id == player_id)
        )
        tp = result.scalar_one_or_none()
        if tp:
            await session.delete(tp)
            await session.commit()
            return True
        return False
