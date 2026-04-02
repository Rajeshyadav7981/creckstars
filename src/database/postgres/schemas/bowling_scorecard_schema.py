from sqlalchemy import Column, Integer, Float, ForeignKey, UniqueConstraint
from src.database.postgres.db import Base


class BowlingScorecardSchema(Base):
    __tablename__ = "bowling_scorecards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    innings_id = Column(Integer, ForeignKey("innings.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    overs_bowled = Column(Float, default=0.0)
    maidens = Column(Integer, default=0)
    runs_conceded = Column(Integer, default=0)
    wickets = Column(Integer, default=0)
    economy_rate = Column(Float, default=0.0)
    wides = Column(Integer, default=0)
    no_balls = Column(Integer, default=0)
    dot_balls = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("innings_id", "player_id", name="uq_bowling_scorecard"),
    )
