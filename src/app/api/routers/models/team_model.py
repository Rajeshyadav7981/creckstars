from pydantic import BaseModel
from datetime import datetime


class CreateTeamRequest(BaseModel):
    name: str
    short_name: str | None = None
    logo_url: str | None = None
    color: str | None = None
    home_ground: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class AddPlayerToTeamRequest(BaseModel):
    player_id: int
    jersey_number: int | None = None
    is_captain: bool = False
    is_vice_captain: bool = False
    is_wicket_keeper: bool = False


class UpdatePlayerRoleRequest(BaseModel):
    is_captain: bool | None = None
    is_vice_captain: bool | None = None
    is_wicket_keeper: bool | None = None
    jersey_number: int | None = None


class TeamResponse(BaseModel):
    id: int
    name: str
    short_name: str | None = None
    logo_url: str | None = None
    color: str | None = None
    home_ground: str | None = None
    created_by: int
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TeamPlayerResponse(BaseModel):
    player_id: int
    first_name: str
    last_name: str | None = None
    full_name: str
    role: str | None = None
    jersey_number: int | None = None
    is_captain: bool = False
    is_vice_captain: bool = False
    is_wicket_keeper: bool = False


class TeamDetailResponse(BaseModel):
    team: TeamResponse
    players: list[TeamPlayerResponse] = []
