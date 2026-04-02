from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, func
from src.database.postgres.db import Base

class TournamentGroupSchema(Base):
    __tablename__ = "tournament_groups"
    id = Column(Integer, primary_key=True, autoincrement=True)
    stage_id = Column(Integer, ForeignKey("tournament_stages.id", ondelete="CASCADE"), nullable=False)
    group_name = Column(String(50), nullable=False)
    group_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("stage_id", "group_name", name="uq_stage_group_name"),)
