from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from src.database.postgres.db import Base

class TournamentGroupTeamSchema(Base):
    __tablename__ = "tournament_group_teams"
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("tournament_groups.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    qualification_status = Column(String(20), nullable=False, default="pending")  # pending, qualified, eliminated
    __table_args__ = (UniqueConstraint("group_id", "team_id", name="uq_group_team"),)
