from sqlalchemy import Column, Integer, String, DateTime, Date, ForeignKey, Text, Boolean, func
from src.database.postgres.db import Base


class MatchSchema(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_code = Column(String(10), unique=True, nullable=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=True)
    team_a_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    team_b_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=True)
    match_date = Column(Date, nullable=True)
    overs = Column(Integer, nullable=False, default=20)
    status = Column(String(20), nullable=False, default="upcoming")  # upcoming, toss, live, completed, abandoned
    toss_winner_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    toss_decision = Column(String(10), nullable=True)  # bat, bowl
    winner_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    result_summary = Column(Text, nullable=True)
    current_innings = Column(Integer, nullable=True, default=0)
    match_type = Column(String(20), nullable=True, default='group')  # group, semi, final
    time_slot = Column(String(50), nullable=True)
    scorer_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    stage_id = Column(Integer, ForeignKey("tournament_stages.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("tournament_groups.id"), nullable=True)
    match_number = Column(Integer, nullable=True)
    result_type = Column(String(20), nullable=True)  # normal, no_result, abandoned, walkover, forfeit, awarded
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
