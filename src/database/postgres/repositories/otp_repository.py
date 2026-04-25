from datetime import datetime, timezone
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.otp_schema import OTPSchema


class OTPRepository:

    @staticmethod
    async def create_otp(session: AsyncSession, data: dict) -> OTPSchema:
        otp = OTPSchema(**data)
        session.add(otp)
        await session.commit()
        return otp

    @staticmethod
    async def get_latest_otp(
        session: AsyncSession, mobile: str, purpose: str
    ) -> OTPSchema | None:
        result = await session.execute(
            select(OTPSchema)
            .where(
                and_(
                    OTPSchema.mobile == mobile,
                    OTPSchema.purpose == purpose,
                    OTPSchema.is_verified == False,
                    OTPSchema.expires_at > datetime.now(timezone.utc),
                )
            )
            .order_by(OTPSchema.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def mark_verified(session: AsyncSession, otp_id: int):
        result = await session.execute(
            select(OTPSchema).where(OTPSchema.id == otp_id)
        )
        otp = result.scalar_one_or_none()
        if otp:
            otp.is_verified = True
            await session.commit()

    @staticmethod
    async def delete_otp(session: AsyncSession, otp_id: int):
        """Remove an OTP row — used to prevent retry after a wrong-code guess."""
        result = await session.execute(
            select(OTPSchema).where(OTPSchema.id == otp_id)
        )
        otp = result.scalar_one_or_none()
        if otp:
            await session.delete(otp)
            await session.commit()
