from sqlalchemy import Column, Integer, String, DateTime, Date, ForeignKey, Text, func
from src.database.postgres.db import Base


class PlayerSchema(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=True)
    full_name = Column(String(200), nullable=False)
    mobile = Column(String(15), nullable=True)
    date_of_birth = Column(Date, nullable=True)
    bio = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state_province = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True)
    batting_style = Column(String(20), nullable=True)  # right_hand, left_hand
    bowling_style = Column(String(30), nullable=True)  # right_arm_fast, left_arm_spin, etc
    role = Column(String(20), nullable=True)  # batsman, bowler, all_rounder, wicket_keeper
    profile_image = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
