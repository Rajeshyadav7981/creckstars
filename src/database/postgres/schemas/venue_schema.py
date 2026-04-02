from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func
from src.database.postgres.db import Base


class VenueSchema(Base):
    __tablename__ = "venues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    city = Column(String(100), nullable=True)
    ground_type = Column(String(50), nullable=True)  # turf, cement, mat
    address = Column(String(500), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
