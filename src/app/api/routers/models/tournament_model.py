from typing import Optional
from pydantic import BaseModel
from datetime import date, datetime


class CreateTournamentRequest(BaseModel):
    name: str
    tournament_type: str = "league"
    overs_per_match: int = 20
    ball_type: str | None = "tennis"
    start_date: date | None = None
    end_date: date | None = None
    venue_id: int | None = None
    organizer_name: Optional[str] = None
    location: Optional[str] = None
    entry_fee: Optional[float] = 0
    prize_pool: Optional[float] = 0
    banner_url: Optional[str] = None
    points_per_win: Optional[int] = 2
    points_per_draw: Optional[int] = 1
    points_per_no_result: Optional[int] = 1
    has_third_place_playoff: Optional[bool] = False


class AddTeamToTournamentRequest(BaseModel):
    team_id: int


class StageConfig(BaseModel):
    name: str  # group_stage, quarter_final, semi_final, final
    qualification_rule: dict | None = None  # {"top_n": 2, "from": "each_group"}


class SetupStagesRequest(BaseModel):
    stages: list[StageConfig]


class GroupConfig(BaseModel):
    name: str  # "Group A"
    team_ids: list[int]


class SetupGroupsRequest(BaseModel):
    groups: list[GroupConfig]


class UpdateTournamentRequest(BaseModel):
    status: Optional[str] = None
    name: Optional[str] = None
    organizer_name: Optional[str] = None
    location: Optional[str] = None
    overs_per_match: Optional[int] = None
    ball_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    entry_fee: Optional[float] = None
    prize_pool: Optional[float] = None
    banner_url: Optional[str] = None
    points_per_win: Optional[int] = None
    points_per_draw: Optional[int] = None
    points_per_no_result: Optional[int] = None
    has_third_place_playoff: Optional[bool] = None


class QualificationRuleRequest(BaseModel):
    top_n: int = 2


class AddTeamToStageRequest(BaseModel):
    team_id: int
    group_id: int


class OverrideMatchRequest(BaseModel):
    winner_id: int
    result_type: str = "walkover"
    reason: str | None = None


class TournamentResponse(BaseModel):
    id: int
    name: str
    tournament_type: str
    overs_per_match: int
    ball_type: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str
    venue_id: int | None = None
    organizer_name: Optional[str] = None
    location: Optional[str] = None
    entry_fee: Optional[float] = 0
    prize_pool: Optional[float] = 0
    banner_url: Optional[str] = None
    created_by: int
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
