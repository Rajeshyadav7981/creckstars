from sqlalchemy import Column, Integer, String, DateTime, Boolean, Index, func
from src.database.postgres.db import Base


class OTPSchema(Base):
    __tablename__ = "otps"
    # Composite index drives get_latest_otp (mobile + purpose + is_verified filter).
    __table_args__ = (
        Index("ix_otp_mobile_purpose_verified", "mobile", "purpose", "is_verified", "expires_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    mobile = Column(String(15), nullable=False, index=True)
    otp_code = Column(String(6), nullable=False)
    is_verified = Column(Boolean, default=False)
    purpose = Column(String(20), nullable=False)  # 'register' | 'login' | 'reset_password'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
