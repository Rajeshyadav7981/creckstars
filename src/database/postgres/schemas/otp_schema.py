from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from src.database.postgres.db import Base


class OTPSchema(Base):
    __tablename__ = "otps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mobile = Column(String(15), nullable=False, index=True)
    otp_code = Column(String(6), nullable=False)
    is_verified = Column(Boolean, default=False)
    purpose = Column(String(20), nullable=False)  # 'register' or 'login'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
