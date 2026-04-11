from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user, get_current_user_optional
from src.services.venue_service import VenueService
from src.app.api.routers.models.venue_model import CreateVenueRequest

router = APIRouter(prefix="/api/venues", tags=["Venues"])


def serialize_venue(v):
    return {
        "id": v.id, "name": v.name, "city": v.city, "ground_type": v.ground_type,
        "address": v.address, "latitude": v.latitude, "longitude": v.longitude,
    }


@router.post("")
async def create_venue(
    req: CreateVenueRequest,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    venue = await VenueService.create_venue(
        session, user.id, req.name, req.city, req.ground_type,
        address=req.address, latitude=req.latitude, longitude=req.longitude,
    )
    return serialize_venue(venue)


@router.get("/search-location")
async def search_location(
    q: str = Query(..., min_length=2, description="Location search query"),
    user=Depends(get_current_user_optional),
):
    """Search locations using OpenStreetMap Nominatim (free, no API key)."""
    results = await VenueService.search_location(q)
    return results


@router.get("/nearby")
async def nearby_venues(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    radius: float = Query(50, ge=1, le=500, description="Radius in km"),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    """Find venues within a radius (km) of given coordinates."""
    rows = await VenueService.get_nearby_venues(session, lat, lng, radius)
    return [
        {
            "id": r["id"], "name": r["name"], "city": r["city"],
            "ground_type": r["ground_type"], "address": r["address"],
            "latitude": r["latitude"], "longitude": r["longitude"],
            "distance_km": round(r["distance_km"], 2),
        }
        for r in rows
    ]


@router.get("")
async def list_venues(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    venues = await VenueService.get_venues(session, created_by=user.id, limit=limit, offset=offset)
    return [serialize_venue(v) for v in venues]


@router.get("/{venue_id}")
async def get_venue(
    venue_id: int,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user_optional),
):
    venue = await VenueService.get_venue(session, venue_id)
    return serialize_venue(venue)
