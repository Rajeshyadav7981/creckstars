import math
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.venue_schema import VenueSchema


class VenueRepository:

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> VenueSchema:
        venue = VenueSchema(**data)
        session.add(venue)
        await session.commit()
        await session.refresh(venue)
        return venue

    @staticmethod
    async def get_by_id(session: AsyncSession, venue_id: int) -> VenueSchema | None:
        result = await session.execute(select(VenueSchema).where(VenueSchema.id == venue_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(session: AsyncSession, created_by: int = None, limit: int = 50, offset: int = 0) -> list:
        query = select(VenueSchema)
        if created_by:
            query = query.where(VenueSchema.created_by == created_by)
        query = query.order_by(VenueSchema.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_nearby(session: AsyncSession, lat: float, lng: float, radius_km: float = 50) -> list:
        """Find venues within radius_km using Haversine formula in SQL."""
        query = text("""
            SELECT *, (
                6371 * acos(
                    cos(radians(:lat)) * cos(radians(latitude))
                    * cos(radians(longitude) - radians(:lng))
                    + sin(radians(:lat)) * sin(radians(latitude))
                )
            ) AS distance_km
            FROM venues
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            HAVING distance_km <= :radius
            ORDER BY distance_km
        """)
        # PostgreSQL doesn't support HAVING without GROUP BY for computed columns,
        # so use a subquery approach instead
        query = text("""
            SELECT * FROM (
                SELECT id, name, city, ground_type, address, latitude, longitude, created_by, created_at,
                    (6371 * acos(
                        LEAST(1.0, cos(radians(:lat)) * cos(radians(latitude))
                        * cos(radians(longitude) - radians(:lng))
                        + sin(radians(:lat)) * sin(radians(latitude)))
                    )) AS distance_km
                FROM venues
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            ) sub
            WHERE distance_km <= :radius
            ORDER BY distance_km
        """)
        result = await session.execute(query, {"lat": lat, "lng": lng, "radius": radius_km})
        return result.mappings().all()
