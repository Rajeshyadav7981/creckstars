from sqlalchemy import Column, Integer, Boolean, ForeignKey, UniqueConstraint
from src.database.postgres.db import Base


class TeamPlayerSchema(Base):
    __tablename__ = "team_players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    jersey_number = Column(Integer, nullable=True)
    is_captain = Column(Boolean, default=False)
    is_vice_captain = Column(Boolean, default=False)
    is_wicket_keeper = Column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("team_id", "player_id", name="uq_team_player"),
    )
