from sqlalchemy import select, or_, case, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from src.database.postgres.schemas.user_schema import UserSchema


class UserRepository:

    @staticmethod
    async def create_user(session: AsyncSession, data: dict) -> UserSchema:
        user = UserSchema(**data)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> UserSchema | None:
        result = await session.execute(
            select(UserSchema).where(UserSchema.email == email)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_mobile(session: AsyncSession, mobile: str) -> UserSchema | None:
        result = await session.execute(
            select(UserSchema).where(UserSchema.mobile == mobile)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: int) -> UserSchema | None:
        result = await session.execute(
            select(UserSchema).where(UserSchema.id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_password(session: AsyncSession, user_id: int, new_hash: str):
        result = await session.execute(
            select(UserSchema).where(UserSchema.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user:
            user.password = new_hash
            await session.commit()

    @staticmethod
    async def update_user(session: AsyncSession, user_id: int, data: dict) -> UserSchema | None:
        result = await session.execute(
            select(UserSchema).where(UserSchema.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return None
        for key, value in data.items():
            setattr(user, key, value)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def search(session: AsyncSession, query: str, limit: int = 20) -> list[tuple]:
        """Search users with priority (username prefix > username contains > name contains) using pg_trgm GIN indexes; returns (user, player_id) rows to avoid N+1 on cricket-profile link."""
        from src.database.postgres.schemas.player_schema import PlayerSchema
        # Subquery: cheapest linked player per user (MIN picks lowest id, which
        # is the first stub/user row ever created). LATERAL would be tidier but
        # GROUP BY is simpler and the player→user mapping is 1:1 in practice.
        player_link = (
            select(
                PlayerSchema.user_id.label("uid"),
                func.min(PlayerSchema.id).label("pid"),
            )
            .where(PlayerSchema.user_id.isnot(None))
            .group_by(PlayerSchema.user_id)
            .subquery()
        )
        q = (
            select(UserSchema, player_link.c.pid)
            .outerjoin(player_link, player_link.c.uid == UserSchema.id)
            .options(load_only(
                UserSchema.id, UserSchema.username, UserSchema.full_name,
                UserSchema.first_name, UserSchema.last_name, UserSchema.profile,
            ))
        )
        if query and query.strip():
            ql = query.strip().lower()
            q = q.where(or_(
                UserSchema.username.ilike(f"{ql}%"),       # prefix (uses varchar_pattern_ops)
                UserSchema.username.ilike(f"%{ql}%"),      # contains (uses GIN trgm)
                UserSchema.full_name.ilike(f"%{ql}%"),     # name (uses GIN trgm)
            )).order_by(
                case(
                    (UserSchema.username.ilike(f"{ql}%"), 0),   # exact prefix first
                    (UserSchema.username.ilike(f"%{ql}%"), 1),  # username contains
                    else_=2,                                     # name match last
                ),
                UserSchema.full_name,
            )
        else:
            q = q.order_by(UserSchema.full_name)
        result = await session.execute(q.limit(limit))
        return list(result.all())
