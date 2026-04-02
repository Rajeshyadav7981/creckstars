from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.innings_schema import InningsSchema
from src.database.postgres.schemas.over_schema import OverSchema


class InningsRepository:

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> InningsSchema:
        innings = InningsSchema(**data)
        session.add(innings)
        await session.commit()
        await session.refresh(innings)
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
        result = await session.execute(select(InningsSchema).where(InningsSchema.id == innings_id))
        innings = result.scalar_one_or_none()
        if not innings:
            return None
        for key, value in data.items():
            setattr(innings, key, value)
        await session.commit()
        await session.refresh(innings)
        return innings

    @staticmethod
    async def create_over(session: AsyncSession, data: dict) -> OverSchema:
        over = OverSchema(**data)
        session.add(over)
        await session.commit()
        await session.refresh(over)
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
        result = await session.execute(select(OverSchema).where(OverSchema.id == over_id))
        over = result.scalar_one_or_none()
        if not over:
            return None
        for key, value in data.items():
            setattr(over, key, value)
        await session.commit()
        await session.refresh(over)
        return over
