from sqlalchemy import Column, Integer, Float, ForeignKey
from src.database.postgres.db import Base


class FallOfWicketSchema(Base):
    __tablename__ = "fall_of_wickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    innings_id = Column(Integer, ForeignKey("innings.id"), nullable=False)
    wicket_number = Column(Integer, nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    runs_at_fall = Column(Integer, nullable=False)
    overs_at_fall = Column(Float, nullable=False)
    delivery_id = Column(Integer, ForeignKey("deliveries.id"), nullable=True)
