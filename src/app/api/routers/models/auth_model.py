from datetime import date
from pydantic import BaseModel


class RegisterRequest(BaseModel):
    first_name: str
    last_name: str
    mobile: str
    email: str | None = None
    password: str
    profile: str | None = None
    username: str | None = None
    bio: str | None = None
    city: str | None = None
    state_province: str | None = None
    country: str | None = None
    date_of_birth: date | None = None
    batting_style: str | None = None
    bowling_style: str | None = None
    player_role: str | None = None


class LoginRequest(BaseModel):
    mobile: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str | None = None
    first_name: str
    last_name: str
    full_name: str
    mobile: str
    email: str | None = None
    profile: str | None = None
    bio: str | None = None
    city: str | None = None
    state_province: str | None = None
    country: str | None = None
    date_of_birth: date | None = None
    batting_style: str | None = None
    bowling_style: str | None = None
    player_role: str | None = None
    followers_count: int = 0
    following_count: int = 0

    model_config = {"from_attributes": True}


class UpdateProfileRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    profile: str | None = None
    bio: str | None = None
    city: str | None = None
    state_province: str | None = None
    country: str | None = None
    date_of_birth: date | None = None
    batting_style: str | None = None
    bowling_style: str | None = None
    player_role: str | None = None
