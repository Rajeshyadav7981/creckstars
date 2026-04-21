"""
UserService — business logic for user operations (follow, profile, search).
Extracted from user_router.py for MVC compliance.
"""
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.user_schema import UserSchema, UserFollowSchema
from src.database.postgres.repositories.user_repository import UserRepository
from src.utils.cache_helper import cache_get, cache_set, cache_delete
from src.utils.logger import get_logger

logger = get_logger(__name__)


class UserService:

    @staticmethod
    async def set_username(session: AsyncSession, user_id: int, username: str):
        """Validate and set username. Invalidates cache."""
        from src.utils.text_parser import validate_username
        username = username.lower().strip()
        valid, err = validate_username(username)
        if not valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)

        existing = await session.execute(
            select(UserSchema.id).where(UserSchema.username == username, UserSchema.id != user_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already taken")

        await session.execute(
            text("UPDATE users SET username = :u WHERE id = :uid"),
            {"u": username, "uid": user_id},
        )
        await session.commit()
        await cache_delete(f"user:{user_id}")
        return username

    @staticmethod
    async def follow(session: AsyncSession, user_id: int, target_id: int):
        """Follow a user. Returns 'followed'."""
        if user_id == target_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot follow yourself")

        target = await UserRepository.get_by_id(session, target_id)
        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        existing = await session.execute(
            select(UserFollowSchema.follower_id).where(
                UserFollowSchema.follower_id == user_id,
                UserFollowSchema.following_id == target_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already following")

        session.add(UserFollowSchema(follower_id=user_id, following_id=target_id))
        await session.execute(
            text("UPDATE users SET following_count = following_count + 1 WHERE id = :uid"),
            {"uid": user_id},
        )
        await session.execute(
            text("UPDATE users SET followers_count = followers_count + 1 WHERE id = :uid"),
            {"uid": target_id},
        )
        await session.commit()
        await UserService._invalidate_follow_caches(session, user_id, target_id)

    @staticmethod
    async def unfollow(session: AsyncSession, user_id: int, target_id: int):
        """Unfollow a user. Returns 'unfollowed'."""
        result = await session.execute(
            select(UserFollowSchema).where(
                UserFollowSchema.follower_id == user_id,
                UserFollowSchema.following_id == target_id,
            )
        )
        follow = result.scalar_one_or_none()
        if not follow:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not following this user")

        await session.delete(follow)
        await session.execute(
            text("UPDATE users SET following_count = GREATEST(0, following_count - 1) WHERE id = :uid"),
            {"uid": user_id},
        )
        await session.execute(
            text("UPDATE users SET followers_count = GREATEST(0, followers_count - 1) WHERE id = :uid"),
            {"uid": target_id},
        )
        await session.commit()
        await UserService._invalidate_follow_caches(session, user_id, target_id)

    @staticmethod
    async def check_follow_status(session: AsyncSession, user_id: int, target_id: int) -> bool:
        """Check if user_id follows target_id."""
        result = await session.execute(
            select(UserFollowSchema.follower_id).where(
                UserFollowSchema.follower_id == user_id,
                UserFollowSchema.following_id == target_id,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def _invalidate_follow_caches(session: AsyncSession, user_id: int, target_id: int):
        """Invalidate all relevant caches after follow/unfollow."""
        keys = [f"user:{user_id}", f"user:{target_id}",
                f"followers:{target_id}", f"following:{user_id}"]
        # Also invalidate profile caches by username
        try:
            from sqlalchemy.orm import load_only
            result = await session.execute(
                select(UserSchema.username).where(UserSchema.id.in_([user_id, target_id]))
            )
            for (uname,) in result.all():
                if uname:
                    keys.append(f"profile:{uname.lower()}")
        except Exception as e:
            logger.warning(f"Failed to get usernames for cache invalidation: {e}")
        await cache_delete(*keys)
