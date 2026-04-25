from pydantic import BaseModel, Field
from datetime import date


class CreateMatchRequest(BaseModel):
    tournament_id: int | None = None
    team_a_id: int = Field(..., gt=0)
    team_b_id: int = Field(..., gt=0)
    venue_id: int | None = None
    match_date: date | None = None
    overs: int = Field(20, gt=0, le=120)
    match_type: str | None = Field("group", max_length=20)
    time_slot: str | None = Field(None, max_length=50)
    stage_id: int | None = None
    group_id: int | None = None


class TossRequest(BaseModel):
    toss_winner_id: int = Field(..., gt=0)
    toss_decision: str = Field(..., pattern="^(bat|bowl)$")


class SquadEntry(BaseModel):
    player_id: int = Field(..., gt=0)
    batting_order: int | None = Field(None, ge=1, le=11)


class SetSquadRequest(BaseModel):
    team_id: int = Field(..., gt=0)
    players: list[SquadEntry] = Field(..., min_length=1, max_length=15)


class StartInningsRequest(BaseModel):
    batting_team_id: int = Field(..., gt=0)
    striker_id: int = Field(..., gt=0)
    non_striker_id: int = Field(..., gt=0)
    bowler_id: int = Field(..., gt=0)
