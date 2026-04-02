from sqlalchemy import Column, Integer, Boolean, ForeignKey, UniqueConstraint
from src.database.postgres.db import Base


class OverSchema(Base):
    __tablename__ = "overs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    innings_id = Column(Integer, ForeignKey("innings.id"), nullable=False)
    over_number = Column(Integer, nullable=False)
    bowler_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    runs_conceded = Column(Integer, default=0)
    wickets = Column(Integer, default=0)
    wides = Column(Integer, default=0)
    no_balls = Column(Integer, default=0)
    is_maiden = Column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("innings_id", "over_number", name="uq_innings_over"),
    )
