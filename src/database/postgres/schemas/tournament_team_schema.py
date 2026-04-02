from sqlalchemy import Column, Integer, ForeignKey, UniqueConstraint
from src.database.postgres.db import Base


class TournamentTeamSchema(Base):
    __tablename__ = "tournament_teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("tournament_id", "team_id", name="uq_tournament_team"),
    )
