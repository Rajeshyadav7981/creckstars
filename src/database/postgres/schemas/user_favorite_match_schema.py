from sqlalchemy import Column, Integer, DateTime, ForeignKey, func
from src.database.postgres.db import Base


class UserFavoriteMatchSchema(Base):
    __tablename__ = "user_favorite_matches"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
