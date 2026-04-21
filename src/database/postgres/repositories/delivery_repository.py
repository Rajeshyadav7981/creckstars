from sqlalchemy import select, func, cast, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.delivery_schema import DeliverySchema


class DeliveryRepository:

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> DeliverySchema:
        delivery = DeliverySchema(**data)
        session.add(delivery)
        await session.flush()
        await session.refresh(delivery)
        return delivery

    @staticmethod
    async def get_by_id(session: AsyncSession, delivery_id: int) -> DeliverySchema | None:
        result = await session.execute(select(DeliverySchema).where(DeliverySchema.id == delivery_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_innings(session: AsyncSession, innings_id: int, over_number: int = None) -> list:
        query = select(DeliverySchema).where(DeliverySchema.innings_id == innings_id)
        if over_number is not None:
            query = query.where(DeliverySchema.over_number == over_number)
        query = query.order_by(DeliverySchema.actual_ball_seq)
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_last_delivery(session: AsyncSession, innings_id: int) -> DeliverySchema | None:
        result = await session.execute(
            select(DeliverySchema)
            .where(DeliverySchema.innings_id == innings_id)
            .order_by(DeliverySchema.actual_ball_seq.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def delete(session: AsyncSession, delivery_id: int) -> bool:
        result = await session.execute(select(DeliverySchema).where(DeliverySchema.id == delivery_id))
        delivery = result.scalar_one_or_none()
        if delivery:
            await session.delete(delivery)
            await session.flush()
            return True
        return False

    @staticmethod
    async def get_next_ball_seq(session: AsyncSession, innings_id: int) -> int:
        result = await session.execute(
            select(func.coalesce(func.max(DeliverySchema.actual_ball_seq), 0))
            .where(DeliverySchema.innings_id == innings_id)
        )
        return result.scalar() + 1

    @staticmethod
    async def get_commentary(session: AsyncSession, innings_id: int, limit: int = 20, offset: int = 0) -> list:
        result = await session.execute(
            select(DeliverySchema)
            .where(DeliverySchema.innings_id == innings_id)
            .order_by(DeliverySchema.actual_ball_seq.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    @staticmethod
    async def get_over_aggregates_for_innings_ids(session: AsyncSession, innings_ids: list) -> list:
        """Bulk per-over roll-up: (innings_id, over_number) -> runs, wickets.
        Drives the Runs per Over + Run Rate charts without shipping every ball to the client."""
        if not innings_ids:
            return []
        q = (
            select(
                DeliverySchema.innings_id,
                DeliverySchema.over_number,
                func.coalesce(func.sum(DeliverySchema.total_runs), 0).label("runs"),
                func.coalesce(func.sum(cast(DeliverySchema.is_wicket, Integer)), 0).label("wickets"),
            )
            .where(DeliverySchema.innings_id.in_(innings_ids))
            .group_by(DeliverySchema.innings_id, DeliverySchema.over_number)
            .order_by(DeliverySchema.innings_id, DeliverySchema.over_number)
        )
        result = await session.execute(q)
        return result.all()
