from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func
from src.database.postgres.db import Base


class TeamSchema(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_code = Column(String(10), unique=True, nullable=True, index=True)
    name = Column(String(200), nullable=False)
    short_name = Column(String(10), nullable=True)
    logo_url = Column(String(500), nullable=True)
    color = Column(String(7), nullable=True)  # hex color
    home_ground = Column(String(200), nullable=True)
    city = Column(String(100), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
