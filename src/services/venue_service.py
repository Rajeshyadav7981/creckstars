import os
import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.venue_repository import VenueRepository
from src.utils.logger import get_logger
from src.utils.http_client import get_http_client

logger = get_logger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
NOMINATIM_HEADERS = {"User-Agent": "CrecKStars/1.0 (support@creckstars)"}
# Bias results to India (the app is India-focused). Set "" for worldwide.
NOMINATIM_COUNTRYCODES = os.getenv("NOMINATIM_COUNTRYCODES", "in")


class VenueService:

    @staticmethod
    async def create_venue(
        session: AsyncSession, user_id: int, name: str,
        city: str = None, ground_type: str = None,
        address: str = None, latitude: float = None, longitude: float = None,
    ):
        return await VenueRepository.create(session, {
            "name": name,
            "city": city,
            "ground_type": ground_type,
            "address": address,
            "latitude": latitude,
            "longitude": longitude,
            "created_by": user_id,
        })

    @staticmethod
    async def get_venue(session: AsyncSession, venue_id: int):
        venue = await VenueRepository.get_by_id(session, venue_id)
        if not venue:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")
        return venue

    @staticmethod
    async def get_venues(session: AsyncSession, created_by: int = None, limit: int = 50, offset: int = 0):
        return await VenueRepository.get_all(session, created_by=created_by, limit=limit, offset=offset)

    @staticmethod
    async def get_nearby_venues(session: AsyncSession, lat: float, lng: float, radius_km: float = 50):
        return await VenueRepository.get_nearby(session, lat, lng, radius_km)

    @staticmethod
    async def search_location(query: str, limit: int = 8):
        """Proxy to OpenStreetMap Nominatim for location autocomplete.

        India-biased for relevance, and degrades to [] on any provider error
        (Nominatim rate-limits at ~1 req/sec) so the autocomplete never hard-fails.
        """
        params = {
            "q": query, "format": "json", "limit": limit,
            "addressdetails": 1, "accept-language": "en",
        }
        if NOMINATIM_COUNTRYCODES:
            params["countrycodes"] = NOMINATIM_COUNTRYCODES
        try:
            resp = await get_http_client().get(
                f"{NOMINATIM_URL}/search", params=params,
                headers=NOMINATIM_HEADERS, timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Nominatim search failed", extra={"extra_data": {"error": str(e)}})
            return []
        return [
                {
                    "display_name": r.get("display_name"),
                    "latitude": float(r.get("lat", 0)),
                    "longitude": float(r.get("lon", 0)),
                    "city": (r.get("address", {}).get("city")
                             or r.get("address", {}).get("town")
                             or r.get("address", {}).get("village")
                             or r.get("address", {}).get("state_district")),
                }
                for r in results
            ]
