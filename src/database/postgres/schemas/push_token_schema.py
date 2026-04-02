from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, func
from src.database.postgres.db import Base


class PushTokenSchema(Base):
    __tablename__ = "push_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expo_push_token = Column(String(255), nullable=False)
    device_type = Column(String(10), nullable=True)  # ios, android, web
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "expo_push_token", name="uq_user_push_token"),
    )


class MatchSubscriptionSchema(Base):
    __tablename__ = "match_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    notify_wickets = Column(String(5), default="true")
    notify_boundaries = Column(String(5), default="false")
    notify_match_events = Column(String(5), default="true")  # start, end, innings break
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "match_id", name="uq_user_match_sub"),
    )
