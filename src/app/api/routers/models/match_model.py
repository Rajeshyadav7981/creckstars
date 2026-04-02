from pydantic import BaseModel
from datetime import date, datetime


class CreateMatchRequest(BaseModel):
    tournament_id: int | None = None
    team_a_id: int
    team_b_id: int
    venue_id: int | None = None
    match_date: date | None = None
    overs: int = 20
    match_type: str | None = "group"
    time_slot: str | None = None
    stage_id: int | None = None
    group_id: int | None = None


class TossRequest(BaseModel):
    toss_winner_id: int
    toss_decision: str  # bat or bowl


class SquadEntry(BaseModel):
    player_id: int
    batting_order: int | None = None


class SetSquadRequest(BaseModel):
    team_id: int
    players: list[SquadEntry]


class StartInningsRequest(BaseModel):
    batting_team_id: int
    striker_id: int
    non_striker_id: int
    bowler_id: int


class MatchResponse(BaseModel):
    id: int
    tournament_id: int | None = None
    team_a_id: int
    team_b_id: int
    venue_id: int | None = None
    match_date: date | None = None
    overs: int
    match_type: str | None = None
    time_slot: str | None = None
    status: str
    toss_winner_id: int | None = None
    toss_decision: str | None = None
    winner_id: int | None = None
    result_summary: str | None = None
    current_innings: int | None = None
    scorer_user_id: int | None = None
    created_by: int
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
