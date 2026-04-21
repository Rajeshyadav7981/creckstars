from typing import Optional
from pydantic import BaseModel, Field
from datetime import date


class CreateTournamentRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    tournament_type: str = Field("league_knockout", max_length=30)
    overs_per_match: int = Field(20, gt=0, le=50)
    ball_type: str | None = Field("tennis", max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    venue_id: int | None = None
    organizer_name: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=500)
    entry_fee: Optional[float] = Field(0, ge=0)
    prize_pool: Optional[float] = Field(0, ge=0)
    banner_url: Optional[str] = Field(None, max_length=500)
    points_per_win: Optional[int] = Field(2, ge=0, le=10)
    points_per_draw: Optional[int] = Field(1, ge=0, le=10)
    points_per_no_result: Optional[int] = Field(1, ge=0, le=10)
    has_third_place_playoff: Optional[bool] = False


class AddTeamToTournamentRequest(BaseModel):
    team_id: int = Field(..., gt=0)


class StageConfig(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    qualification_rule: dict | None = None


class SetupStagesRequest(BaseModel):
    stages: list[StageConfig] = Field(..., min_length=1, max_length=10)


class GroupConfig(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    team_ids: list[int] = Field(..., min_length=1)


class SetupGroupsRequest(BaseModel):
    groups: list[GroupConfig] = Field(..., min_length=1, max_length=20)


class UpdateTournamentRequest(BaseModel):
    status: Optional[str] = Field(None, max_length=20)
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    organizer_name: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=500)
    overs_per_match: Optional[int] = Field(None, gt=0, le=50)
    ball_type: Optional[str] = Field(None, max_length=20)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    entry_fee: Optional[float] = Field(None, ge=0)
    prize_pool: Optional[float] = Field(None, ge=0)
    banner_url: Optional[str] = Field(None, max_length=500)
    points_per_win: Optional[int] = Field(None, ge=0, le=10)
    points_per_draw: Optional[int] = Field(None, ge=0, le=10)
    points_per_no_result: Optional[int] = Field(None, ge=0, le=10)
    has_third_place_playoff: Optional[bool] = None


class QualificationRuleRequest(BaseModel):
    top_n: int = Field(2, gt=0, le=20)


class OverrideMatchRequest(BaseModel):
    winner_id: int = Field(..., gt=0)
    result_type: str = Field("walkover", max_length=20)
    reason: str | None = Field(None, max_length=300)
