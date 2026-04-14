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
