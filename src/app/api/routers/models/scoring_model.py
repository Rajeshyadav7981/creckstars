from pydantic import BaseModel


class ScoreDeliveryRequest(BaseModel):
    batsman_runs: int = 0
    is_boundary: bool = False
    is_six: bool = False
    extra_type: str | None = None  # wide, noball, bye, legbye
    extra_runs: int = 0
    is_wicket: bool = False
    wicket_type: str | None = None  # bowled, caught, lbw, run_out, stumped, hit_wicket
    dismissed_player_id: int | None = None
    fielder_id: int | None = None
    new_batsman_id: int | None = None


class EndOverRequest(BaseModel):
    next_bowler_id: int


class MatchStatusRequest(BaseModel):
    reason: str | None = None


class BroadcastMessageRequest(BaseModel):
    message: str


class LiveStateResponse(BaseModel):
    match_id: int
    innings_number: int
    batting_team_id: int
    bowling_team_id: int
    total_runs: int
    total_wickets: int
    total_overs: float
    current_over: int
    current_ball: int
    target: int | None = None
    run_rate: float = 0.0
    required_rate: float | None = None
    striker: dict | None = None
    non_striker: dict | None = None
    bowler: dict | None = None
    this_over: list = []
    last_wicket: str | None = None
