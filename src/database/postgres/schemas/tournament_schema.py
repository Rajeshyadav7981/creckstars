from sqlalchemy import Column, Integer, String, DateTime, Date, Float, Boolean, ForeignKey, func
from src.database.postgres.db import Base


class TournamentSchema(Base):
    __tablename__ = "tournaments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tournament_code = Column(String(10), unique=True, nullable=True, index=True)
    name = Column(String(200), nullable=False)
    tournament_type = Column(String(20), nullable=False, default="league_knockout")  # only league_knockout is supported; legacy rows may still be league/knockout
    overs_per_match = Column(Integer, nullable=False, default=20)
    ball_type = Column(String(20), nullable=True, default="tennis")  # tennis, leather, rubber
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    status = Column(String(20), nullable=False, default="upcoming")  # upcoming, live, completed
    organizer_name = Column(String(200), nullable=True)
    location = Column(String(500), nullable=True)
    entry_fee = Column(Float, nullable=True, default=0)
    prize_pool = Column(Float, nullable=True, default=0)
    banner_url = Column(String(500), nullable=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=True)
    points_per_win = Column(Integer, nullable=False, default=2)
    points_per_draw = Column(Integer, nullable=False, default=1)
    points_per_no_result = Column(Integer, nullable=False, default=0)
    has_third_place_playoff = Column(Boolean, nullable=False, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
