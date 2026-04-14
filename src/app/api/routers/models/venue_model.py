from pydantic import BaseModel


class CreateVenueRequest(BaseModel):
    name: str
    city: str | None = None
    ground_type: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
