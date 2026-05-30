from sqlalchemy import select, text, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.postgres.schemas.user_favorite_match_schema import UserFavoriteMatchSchema
from src.database.postgres.schemas.user_favorite_tournament_schema import UserFavoriteTournamentSchema


class FavoriteRepository:
    @staticmethod
    async def add_match(session: AsyncSession, user_id: int, match_id: int) -> bool:
        stmt = pg_insert(UserFavoriteMatchSchema).values(
            user_id=user_id, match_id=match_id,
        ).on_conflict_do_nothing(index_elements=["user_id", "match_id"])
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0

    @staticmethod
    async def remove_match(session: AsyncSession, user_id: int, match_id: int) -> bool:
        result = await session.execute(
            delete(UserFavoriteMatchSchema).where(
                UserFavoriteMatchSchema.user_id == user_id,
                UserFavoriteMatchSchema.match_id == match_id,
            )
        )
        await session.commit()
        return result.rowcount > 0

    @staticmethod
    async def is_match_favorite(session: AsyncSession, user_id: int, match_id: int) -> bool:
        result = await session.execute(
            select(UserFavoriteMatchSchema.match_id).where(
                UserFavoriteMatchSchema.user_id == user_id,
                UserFavoriteMatchSchema.match_id == match_id,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def list_match_ids(session: AsyncSession, user_id: int) -> list[int]:
        result = await session.execute(
            select(UserFavoriteMatchSchema.match_id)
            .where(UserFavoriteMatchSchema.user_id == user_id)
            .order_by(UserFavoriteMatchSchema.created_at.desc())
        )
        return [r for (r,) in result.all()]

    @staticmethod
    async def add_tournament(session: AsyncSession, user_id: int, tournament_id: int) -> bool:
        stmt = pg_insert(UserFavoriteTournamentSchema).values(
            user_id=user_id, tournament_id=tournament_id,
        ).on_conflict_do_nothing(index_elements=["user_id", "tournament_id"])
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0

    @staticmethod
    async def remove_tournament(session: AsyncSession, user_id: int, tournament_id: int) -> bool:
        result = await session.execute(
            delete(UserFavoriteTournamentSchema).where(
                UserFavoriteTournamentSchema.user_id == user_id,
                UserFavoriteTournamentSchema.tournament_id == tournament_id,
            )
        )
        await session.commit()
        return result.rowcount > 0

    @staticmethod
    async def is_tournament_favorite(session: AsyncSession, user_id: int, tournament_id: int) -> bool:
        result = await session.execute(
            select(UserFavoriteTournamentSchema.tournament_id).where(
                UserFavoriteTournamentSchema.user_id == user_id,
                UserFavoriteTournamentSchema.tournament_id == tournament_id,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def list_tournament_ids(session: AsyncSession, user_id: int) -> list[int]:
        result = await session.execute(
            select(UserFavoriteTournamentSchema.tournament_id)
            .where(UserFavoriteTournamentSchema.user_id == user_id)
            .order_by(UserFavoriteTournamentSchema.created_at.desc())
        )
        return [r for (r,) in result.all()]

    @staticmethod
    async def all_ids(session: AsyncSession, user_id: int) -> dict:
        match_ids = await FavoriteRepository.list_match_ids(session, user_id)
        tournament_ids = await FavoriteRepository.list_tournament_ids(session, user_id)
        return {"match_ids": match_ids, "tournament_ids": tournament_ids}
