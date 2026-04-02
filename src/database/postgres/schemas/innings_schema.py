from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint, Boolean, func
from src.database.postgres.db import Base


class InningsSchema(Base):
    __tablename__ = "innings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    innings_number = Column(Integer, nullable=False)  # 1 or 2
    batting_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    bowling_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    total_runs = Column(Integer, default=0)
    total_wickets = Column(Integer, default=0)
    total_overs = Column(Float, default=0.0)
    total_extras = Column(Integer, default=0)
    status = Column(String(20), default="not_started")  # not_started, in_progress, completed
    target = Column(Integer, nullable=True)
    current_over = Column(Integer, default=0)
    current_ball = Column(Integer, default=0)
    current_striker_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    current_non_striker_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    current_bowler_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    is_free_hit = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("match_id", "innings_number", name="uq_match_innings"),
    )
