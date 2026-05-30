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
        """Venues within radius_km of (lat, lng), nearest first.

        Hits the GIST ix_venues_earth index via earth_box; earth_distance trims
        the bounding box to a circle and provides the sort key.
        """
        radius_m = float(radius_km) * 1000.0
        query = text(
            """
            SELECT id, name, city, ground_type, address, latitude, longitude,
                   created_by, created_at,
                   earth_distance(
                       ll_to_earth(:lat, :lng),
                       ll_to_earth(latitude, longitude)
                   ) / 1000.0 AS distance_km
            FROM venues
            WHERE latitude IS NOT NULL
              AND longitude IS NOT NULL
              AND ll_to_earth(latitude, longitude)
                  <@ earth_box(ll_to_earth(:lat, :lng), :radius_m)
              AND earth_distance(
                    ll_to_earth(:lat, :lng),
                    ll_to_earth(latitude, longitude)
                  ) <= :radius_m
            ORDER BY distance_km
            """
        )
        result = await session.execute(query, {"lat": lat, "lng": lng, "radius_m": radius_m})
        return result.mappings().all()
