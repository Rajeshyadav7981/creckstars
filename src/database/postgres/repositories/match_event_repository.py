from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.match_event_schema import MatchEventSchema


class MatchEventRepository:

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> MatchEventSchema:
        event = MatchEventSchema(**data)
        session.add(event)
        await session.flush()
        return event

    @staticmethod
    async def get_last_event(session: AsyncSession, match_id: int) -> MatchEventSchema | None:
        result = await session.execute(
            select(MatchEventSchema)
            .where(MatchEventSchema.match_id == match_id, MatchEventSchema.is_undone == False)
            .order_by(MatchEventSchema.sequence_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_next_sequence(session: AsyncSession, match_id: int) -> int:
        result = await session.execute(
            select(func.coalesce(func.max(MatchEventSchema.sequence_number), 0))
            .where(MatchEventSchema.match_id == match_id)
        )
        return result.scalar() + 1

    @staticmethod
    async def mark_undone(session: AsyncSession, event_id: int):
        result = await session.execute(select(MatchEventSchema).where(MatchEventSchema.id == event_id))
        event = result.scalar_one_or_none()
        if event:
            event.is_undone = True
            await session.flush()

    @staticmethod
    async def get_events(session: AsyncSession, match_id: int, limit: int = 50) -> list:
        result = await session.execute(
            select(MatchEventSchema)
            .where(MatchEventSchema.match_id == match_id, MatchEventSchema.is_undone == False)
            .order_by(MatchEventSchema.sequence_number.desc())
            .limit(limit)
        )
        return result.scalars().all()
