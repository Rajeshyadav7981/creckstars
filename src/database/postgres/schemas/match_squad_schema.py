from sqlalchemy import Column, Integer, Boolean, ForeignKey, UniqueConstraint
from src.database.postgres.db import Base


class MatchSquadSchema(Base):
    __tablename__ = "match_squads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    is_playing = Column(Boolean, default=True)
    batting_order = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("match_id", "team_id", "player_id", name="uq_match_squad"),
    )
