from pydantic import BaseModel
from datetime import date


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
