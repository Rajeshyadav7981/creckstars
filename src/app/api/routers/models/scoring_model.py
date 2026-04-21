from pydantic import BaseModel, Field


class ScoreDeliveryRequest(BaseModel):
    batsman_runs: int = Field(0, ge=0, le=7)
    is_boundary: bool = False
    is_six: bool = False
    extra_type: str | None = Field(None, pattern="^(wide|noball|bye|legbye)$")
    extra_runs: int = Field(0, ge=0, le=7)
    is_wicket: bool = False
    wicket_type: str | None = Field(None, pattern="^(bowled|caught|lbw|run_out|stumped|hit_wicket)$")
    dismissed_player_id: int | None = None
    fielder_id: int | None = None
    new_batsman_id: int | None = None
    # Optional enrichments from ShotZonePicker (scorer-authored per-ball metadata).
    commentary: str | None = Field(None, max_length=300)
    field_zone: str | None = Field(None, max_length=32)
    batting_hand: str | None = Field(None, pattern="^(left|right)$")


class EndOverRequest(BaseModel):
    next_bowler_id: int = Field(..., gt=0)


class MatchStatusRequest(BaseModel):
    reason: str | None = Field(None, max_length=300)


class BroadcastMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=200)
