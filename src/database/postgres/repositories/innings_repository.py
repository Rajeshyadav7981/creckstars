from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.innings_schema import InningsSchema
from src.database.postgres.schemas.over_schema import OverSchema


class InningsRepository:
    """Repositories flush, services commit — mutating methods use session.flush() so services keep control of the transaction boundary (essential for FOR UPDATE locks in ScoringService)."""

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> InningsSchema:
        innings = InningsSchema(**data)
        session.add(innings)
        await session.flush()
        return innings

    @staticmethod
    async def get_by_id(session: AsyncSession, innings_id: int) -> InningsSchema | None:
        result = await session.execute(select(InningsSchema).where(InningsSchema.id == innings_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_match(session: AsyncSession, match_id: int, innings_number: int = None) -> list:
        query = select(InningsSchema).where(InningsSchema.match_id == match_id)
        if innings_number:
            query = query.where(InningsSchema.innings_number == innings_number)
        query = query.order_by(InningsSchema.innings_number)
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def update(session: AsyncSession, innings_id: int, data: dict) -> InningsSchema | None:
        """Partial update; uses session.get() so the returned instance is always ORM-attached."""
        if not data:
            return None
        innings = await session.get(InningsSchema, innings_id)
        if innings is None:
            return None
        for key, value in data.items():
            setattr(innings, key, value)
        await session.flush()
        return innings

    @staticmethod
    async def create_over(session: AsyncSession, data: dict) -> OverSchema:
        over = OverSchema(**data)
        session.add(over)
        await session.flush()
        return over

    @staticmethod
    async def get_over(session: AsyncSession, innings_id: int, over_number: int) -> OverSchema | None:
        result = await session.execute(
            select(OverSchema)
            .where(OverSchema.innings_id == innings_id, OverSchema.over_number == over_number)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_over(session: AsyncSession, over_id: int, data: dict) -> OverSchema | None:
        if not data:
            return None
        over = await session.get(OverSchema, over_id)
        if over is None:
            return None
        for key, value in data.items():
            setattr(over, key, value)
        await session.flush()
        return None if over is None else over
