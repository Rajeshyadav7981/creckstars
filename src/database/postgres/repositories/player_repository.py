from sqlalchemy import select, or_, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.player_schema import PlayerSchema


class PlayerRepository:

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> PlayerSchema:
        player = PlayerSchema(**data)
        session.add(player)
        await session.commit()
        await session.refresh(player)
        return player

    @staticmethod
    async def get_by_id(session: AsyncSession, player_id: int) -> PlayerSchema | None:
        result = await session.execute(select(PlayerSchema).where(PlayerSchema.id == player_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(session: AsyncSession, search: str = None, created_by: int = None, limit: int = 50, offset: int = 0) -> list:
        from sqlalchemy.orm import load_only
        query = select(PlayerSchema).options(load_only(
            PlayerSchema.id, PlayerSchema.full_name, PlayerSchema.first_name,
            PlayerSchema.last_name, PlayerSchema.role, PlayerSchema.mobile,
            PlayerSchema.created_at,
        ))
        if created_by:
            query = query.where(PlayerSchema.created_by == created_by)
        if search:
            query = query.where(
                or_(
                    PlayerSchema.full_name.ilike(f"%{search}%"),
                    PlayerSchema.mobile.ilike(f"%{search}%"),
                )
            )
        query = query.order_by(PlayerSchema.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_by_user_id(session: AsyncSession, user_id: int) -> PlayerSchema | None:
        result = await session.execute(select(PlayerSchema).where(PlayerSchema.user_id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_stubs_by_mobile(session: AsyncSession, mobile: str) -> list[PlayerSchema]:
        """Find unlinked (user_id IS NULL) player stubs with the given mobile."""
        result = await session.execute(
            select(PlayerSchema).where(
                PlayerSchema.mobile == mobile,
                PlayerSchema.user_id.is_(None),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def link_stubs_to_user(
        session: AsyncSession,
        mobile: str,
        user_id: int,
        sync_fields: dict | None = None,
    ) -> int:
        """Attach user_id to every stub player with this mobile; sync_fields normalises identity columns to the user's registered values. Returns rows updated; caller commits."""
        values = {"user_id": user_id}
        if sync_fields:
            for k, v in sync_fields.items():
                if v:
                    values[k] = v
        res = await session.execute(
            sql_update(PlayerSchema)
            .where(
                PlayerSchema.mobile == mobile,
                PlayerSchema.user_id.is_(None),
            )
            .values(**values)
        )
        return int(res.rowcount or 0)

    @staticmethod
    async def update(session: AsyncSession, player_id: int, data: dict) -> PlayerSchema | None:
        result = await session.execute(select(PlayerSchema).where(PlayerSchema.id == player_id))
        player = result.scalar_one_or_none()
        if not player:
            return None
        for key, value in data.items():
            if value is not None:
                setattr(player, key, value)
        await session.commit()
        await session.refresh(player)
        return player
