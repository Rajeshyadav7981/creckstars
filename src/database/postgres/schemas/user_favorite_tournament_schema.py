from sqlalchemy import Column, Integer, DateTime, ForeignKey, func
from src.database.postgres.db import Base


class UserFavoriteTournamentSchema(Base):
    __tablename__ = "user_favorite_tournaments"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
