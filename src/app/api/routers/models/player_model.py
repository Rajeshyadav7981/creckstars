from pydantic import BaseModel
from datetime import datetime, date


class CreatePlayerRequest(BaseModel):
    first_name: str
    last_name: str | None = None
    mobile: str | None = None
    date_of_birth: date | None = None
    bio: str | None = None
    city: str | None = None
    state_province: str | None = None
    country: str | None = None
    batting_style: str | None = None
    bowling_style: str | None = None
    role: str | None = None
    profile_image: str | None = None
    user_id: int | None = None


class UpdatePlayerRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    mobile: str | None = None
    date_of_birth: date | None = None
    bio: str | None = None
    city: str | None = None
    state_province: str | None = None
    country: str | None = None
    batting_style: str | None = None
    bowling_style: str | None = None
    role: str | None = None
    profile_image: str | None = None


class PlayerResponse(BaseModel):
    id: int
    user_id: int | None = None
    first_name: str
    last_name: str | None = None
    full_name: str
    mobile: str | None = None
    date_of_birth: date | None = None
    bio: str | None = None
    city: str | None = None
    state_province: str | None = None
    country: str | None = None
    batting_style: str | None = None
    bowling_style: str | None = None
    role: str | None = None
    profile_image: str | None = None
    created_by: int
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
