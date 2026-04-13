from datetime import timedelta
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.user_repository import UserRepository
from src.utils.security import hash_password, verify_password, needs_rehash, create_access_token, create_refresh_token
from src.app.api.config import ACCESS_TOKEN_EXPIRE_MINUTES
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _user_response(user, token, refresh_token):
    return {
        "access_token": token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": getattr(user, 'username', None),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
            "mobile": user.mobile,
            "email": user.email,
            "profile": user.profile,
        },
    }


class AuthService:

    @staticmethod
    async def register(
        session: AsyncSession,
        first_name: str,
        last_name: str,
        mobile: str,
        email: str | None,
        password: str,
        profile: str | None = None,
        username: str | None = None,
    ) -> dict:
        """Register user. OTP must be verified before calling this."""
        # Check duplicates
        if email and await UserRepository.get_by_email(session, email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )
        if await UserRepository.get_by_mobile(session, mobile):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mobile number already registered",
            )

        # Validate and check username
        if username:
            username = username.lower().strip()
            from src.utils.text_parser import validate_username
            valid, err = validate_username(username)
            if not valid:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
            from sqlalchemy import select
            from src.database.postgres.schemas.user_schema import UserSchema
            existing = await session.execute(select(UserSchema.id).where(UserSchema.username == username))
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already taken")
        else:
            # Auto-generate from name
            from src.utils.text_parser import generate_username
            import random
            username = generate_username(first_name, random.randint(1000, 9999))

        user = await UserRepository.create_user(session, {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": f"{first_name} {last_name}",
            "mobile": mobile,
            "email": email,
            "password": hash_password(password),
            "profile": profile,
            "username": username,
        })

        token = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        )
        refresh = create_refresh_token(user.id)

        logger.info("User registered", extra={"extra_data": {
            "user_id": user.id, "mobile": mobile,
        }})

        return _user_response(user, token, refresh)

    @staticmethod
    async def update_profile(
        session: AsyncSession,
        user_id: int,
        data: dict,
    ) -> dict:
        """Update user profile fields."""
        update_data = {}
        if "first_name" in data and data["first_name"]:
            update_data["first_name"] = data["first_name"]
        if "last_name" in data and data["last_name"]:
            update_data["last_name"] = data["last_name"]
        if "email" in data:
            update_data["email"] = data["email"]
        if "profile" in data:
            update_data["profile"] = data["profile"] or None

        # Rebuild full_name if name changed
        if "first_name" in update_data or "last_name" in update_data:
            current_user = await UserRepository.get_by_id(session, user_id)
            fn = update_data.get("first_name", current_user.first_name)
            ln = update_data.get("last_name", current_user.last_name)
            update_data["full_name"] = f"{fn} {ln}"

        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update",
            )

        # Check email uniqueness if changing email
        if "email" in update_data and update_data["email"]:
            existing = await UserRepository.get_by_email(session, update_data["email"])
            if existing and existing.id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already in use",
                )

        user = await UserRepository.update_user(session, user_id, update_data)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Invalidate Redis user cache so stale name/photo is not served
        try:
            from src.database.redis.redis_client import redis_client
            r = await redis_client.get_client()
            if r:
                await r.delete(f"user:{user_id}")
        except Exception:
            pass

        return {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
            "mobile": user.mobile,
            "email": user.email,
            "profile": user.profile,
        }

    @staticmethod
    async def login(
        session: AsyncSession,
        mobile: str,
        password: str,
    ) -> dict:
        """Login with password. OTP must be verified before calling this."""
        user = await UserRepository.get_by_mobile(session, mobile)
        if not user:
            logger.info("Login failed: user not found", extra={"extra_data": {
                "mobile": mobile, "success": False,
            }})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No account found with this mobile number",
            )

        if not verify_password(password, user.password):
            logger.info("Login failed: invalid password", extra={"extra_data": {
                "mobile": mobile, "success": False,
            }})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid password. Please try again.",
            )

        # Auto-rehash legacy SHA-256 passwords to bcrypt on successful login
        if needs_rehash(user.password):
            new_hash = hash_password(password)
            await UserRepository.update_password(session, user.id, new_hash)

        token = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        )
        refresh = create_refresh_token(user.id)

        logger.info("Login successful", extra={"extra_data": {
            "user_id": user.id, "mobile": mobile, "success": True,
        }})

        return _user_response(user, token, refresh)
