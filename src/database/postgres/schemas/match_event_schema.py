from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from src.database.postgres.db import Base


class MatchEventSchema(Base):
    __tablename__ = "match_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    event_type = Column(String(20), nullable=False)  # delivery, wicket, over_end, innings_end, undo, toss, match_end
    event_data = Column(JSONB, nullable=True)
    match_state = Column(JSONB, nullable=True)  # snapshot for undo
    sequence_number = Column(Integer, nullable=False)
    is_undone = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_match_events_match_seq", "match_id", "sequence_number"),
    )
