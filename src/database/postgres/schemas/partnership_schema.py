from sqlalchemy import Column, Integer, Boolean, ForeignKey
from src.database.postgres.db import Base


class PartnershipSchema(Base):
    __tablename__ = "partnerships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    innings_id = Column(Integer, ForeignKey("innings.id"), nullable=False)
    wicket_number = Column(Integer, nullable=False)
    player_a_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    player_b_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    total_runs = Column(Integer, default=0)
    total_balls = Column(Integer, default=0)
    player_a_runs = Column(Integer, default=0)
    player_b_runs = Column(Integer, default=0)
    extras = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
