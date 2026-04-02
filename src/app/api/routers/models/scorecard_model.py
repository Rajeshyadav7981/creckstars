from pydantic import BaseModel


class BattingCardResponse(BaseModel):
    player_id: int
    player_name: str | None = None
    batting_position: int | None = None
    runs: int = 0
    balls_faced: int = 0
    fours: int = 0
    sixes: int = 0
    strike_rate: float = 0.0
    how_out: str | None = None
    is_out: bool = False

    model_config = {"from_attributes": True}


class BowlingCardResponse(BaseModel):
    player_id: int
    player_name: str | None = None
    overs_bowled: float = 0.0
    maidens: int = 0
    runs_conceded: int = 0
    wickets: int = 0
    economy_rate: float = 0.0
    wides: int = 0
    no_balls: int = 0
    dot_balls: int = 0

    model_config = {"from_attributes": True}


class InningsScorecardResponse(BaseModel):
    innings_number: int
    batting_team_id: int
    bowling_team_id: int
    total_runs: int
    total_wickets: int
    total_overs: float
    total_extras: int
    batting: list[BattingCardResponse] = []
    bowling: list[BowlingCardResponse] = []
    fall_of_wickets: list = []
    partnerships: list = []


class FullScorecardResponse(BaseModel):
    match_id: int
    status: str
    innings: list[InningsScorecardResponse] = []
