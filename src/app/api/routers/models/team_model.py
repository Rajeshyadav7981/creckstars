from pydantic import BaseModel, Field


class CreateTeamRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    short_name: str | None = Field(None, max_length=10)
    logo_url: str | None = Field(None, max_length=500)
    color: str | None = Field(None, max_length=7)  # hex color
    home_ground: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=100)
    latitude: float | None = None
    longitude: float | None = None


class AddPlayerToTeamRequest(BaseModel):
    player_id: int = Field(..., gt=0)
    jersey_number: int | None = Field(None, ge=0, le=999)
    is_captain: bool = False
    is_vice_captain: bool = False
    is_wicket_keeper: bool = False


class UpdatePlayerRoleRequest(BaseModel):
    is_captain: bool | None = None
    is_vice_captain: bool | None = None
    is_wicket_keeper: bool | None = None
    jersey_number: int | None = Field(None, ge=0, le=999)
