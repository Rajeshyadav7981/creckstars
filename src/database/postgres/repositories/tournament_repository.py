from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from src.database.postgres.schemas.tournament_schema import TournamentSchema
from src.database.postgres.schemas.tournament_team_schema import TournamentTeamSchema
from src.database.postgres.schemas.team_schema import TeamSchema


class TournamentRepository:

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> TournamentSchema:
        tournament = TournamentSchema(**data)
        session.add(tournament)
        await session.commit()
        await session.refresh(tournament)
        return tournament

    @staticmethod
    async def get_by_id(session: AsyncSession, tournament_id: int) -> TournamentSchema | None:
        result = await session.execute(
            select(TournamentSchema).where(TournamentSchema.id == tournament_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_code(session: AsyncSession, code: str) -> TournamentSchema | None:
        result = await session.execute(select(TournamentSchema).where(TournamentSchema.tournament_code == code))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(
        session: AsyncSession, status: str = None, created_by: int = None,
        search: str = None, limit: int = 50, offset: int = 0,
    ) -> list:
        from sqlalchemy import or_
        query = select(TournamentSchema).options(load_only(
            TournamentSchema.id, TournamentSchema.tournament_code, TournamentSchema.name,
            TournamentSchema.tournament_type, TournamentSchema.overs_per_match,
            TournamentSchema.ball_type, TournamentSchema.start_date, TournamentSchema.end_date,
            TournamentSchema.status, TournamentSchema.organizer_name, TournamentSchema.location,
            TournamentSchema.entry_fee, TournamentSchema.prize_pool, TournamentSchema.banner_url,
            TournamentSchema.created_by, TournamentSchema.created_at,
        ))
        if status:
            query = query.where(TournamentSchema.status == status)
        if created_by:
            query = query.where(TournamentSchema.created_by == created_by)
        if search:
            query = query.where(or_(
                TournamentSchema.name.ilike(f"%{search}%"),
                TournamentSchema.tournament_code.ilike(f"%{search}%"),
            ))
        query = query.order_by(TournamentSchema.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def update(session: AsyncSession, tournament_id: int, data: dict) -> TournamentSchema | None:
        result = await session.execute(
            select(TournamentSchema).where(TournamentSchema.id == tournament_id)
        )
        tournament = result.scalar_one_or_none()
        if not tournament:
            return None
        for key, value in data.items():
            if value is not None:
                setattr(tournament, key, value)
        await session.commit()
        await session.refresh(tournament)
        return tournament

    @staticmethod
    async def add_team(session: AsyncSession, tournament_id: int, team_id: int) -> TournamentTeamSchema:
        tt = TournamentTeamSchema(tournament_id=tournament_id, team_id=team_id)
        session.add(tt)
        await session.commit()
        await session.refresh(tt)
        return tt

    @staticmethod
    async def get_teams(session: AsyncSession, tournament_id: int) -> list:
        result = await session.execute(
            select(TeamSchema).options(load_only(
                TeamSchema.id, TeamSchema.name, TeamSchema.short_name,
                TeamSchema.team_code, TeamSchema.logo_url, TeamSchema.color,
            ))
            .join(TournamentTeamSchema, TeamSchema.id == TournamentTeamSchema.team_id)
            .where(TournamentTeamSchema.tournament_id == tournament_id)
        )
        return result.scalars().all()

    @staticmethod
    async def remove_team(session: AsyncSession, tournament_id: int, team_id: int) -> bool:
        result = await session.execute(
            select(TournamentTeamSchema)
            .where(TournamentTeamSchema.tournament_id == tournament_id, TournamentTeamSchema.team_id == team_id)
        )
        tt = result.scalar_one_or_none()
        if tt:
            await session.delete(tt)
            await session.commit()
            return True
        return False
