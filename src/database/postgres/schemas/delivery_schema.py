from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func
from src.database.postgres.db import Base


class DeliverySchema(Base):
    __tablename__ = "deliveries"
    # Enforce one ball per (innings, sequence) at DB level. Belt-and-suspenders
    # for the row-lock in ScoringService.record_delivery — if the lock ever
    # fails to serialize (e.g. pgBouncer in transaction-pooling mode + bug),
    # Postgres still refuses to persist a duplicate sequence number.
    __table_args__ = (
        UniqueConstraint("innings_id", "actual_ball_seq", name="ux_deliveries_innings_seq"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    innings_id = Column(Integer, ForeignKey("innings.id"), nullable=False)
    over_number = Column(Integer, nullable=False)
    ball_number = Column(Integer, nullable=False)  # legal ball 1-6
    actual_ball_seq = Column(Integer, nullable=False)  # includes extras
    striker_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    non_striker_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    bowler_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    batsman_runs = Column(Integer, default=0)  # 0-6
    is_boundary = Column(Boolean, default=False)
    is_six = Column(Boolean, default=False)
    extra_type = Column(String(10), nullable=True)  # wide, noball, bye, legbye, null
    extra_runs = Column(Integer, default=0)
    total_runs = Column(Integer, default=0)  # batsman_runs + extra_runs
    is_wicket = Column(Boolean, default=False)
    wicket_type = Column(String(20), nullable=True)  # bowled, caught, lbw, run_out, stumped, hit_wicket
    dismissed_player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    fielder_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    is_legal = Column(Boolean, default=True)
    commentary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
