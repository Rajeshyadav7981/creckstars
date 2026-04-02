from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, UniqueConstraint
from src.database.postgres.db import Base


class BattingScorecardSchema(Base):
    __tablename__ = "batting_scorecards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    innings_id = Column(Integer, ForeignKey("innings.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    batting_position = Column(Integer, nullable=True)
    runs = Column(Integer, default=0)
    balls_faced = Column(Integer, default=0)
    fours = Column(Integer, default=0)
    sixes = Column(Integer, default=0)
    strike_rate = Column(Float, default=0.0)
    how_out = Column(String(100), nullable=True)
    is_out = Column(Boolean, default=False)
    bowler_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    fielder_id = Column(Integer, ForeignKey("players.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("innings_id", "player_id", name="uq_batting_scorecard"),
    )
