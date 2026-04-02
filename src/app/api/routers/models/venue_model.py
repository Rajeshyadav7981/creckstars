from pydantic import BaseModel
from datetime import datetime


class CreateVenueRequest(BaseModel):
    name: str
    city: str | None = None
    ground_type: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class VenueResponse(BaseModel):
    id: int
    name: str
    city: str | None = None
    ground_type: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    created_by: int
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
