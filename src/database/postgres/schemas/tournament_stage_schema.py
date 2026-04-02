from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from src.database.postgres.db import Base

class TournamentStageSchema(Base):
    __tablename__ = "tournament_stages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False)
    stage_name = Column(String(50), nullable=False)  # group_stage, quarter_final, semi_final, final
    stage_order = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="upcoming")  # upcoming, in_progress, completed
    qualification_rule = Column(JSONB, nullable=True)  # {"top_n": 2, "from": "each_group"}
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tournament_id", "stage_order", name="uq_tournament_stage_order"),)
