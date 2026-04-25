from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.batting_scorecard_schema import BattingScorecardSchema
from src.database.postgres.schemas.bowling_scorecard_schema import BowlingScorecardSchema
from src.database.postgres.schemas.fall_of_wicket_schema import FallOfWicketSchema
from src.database.postgres.schemas.partnership_schema import PartnershipSchema


class ScorecardRepository:
    """Repositories flush, services commit. See InningsRepository docstring."""

    @staticmethod
    async def get_or_create_batting(session: AsyncSession, innings_id: int, player_id: int, position: int = None) -> BattingScorecardSchema:
        result = await session.execute(
            select(BattingScorecardSchema)
            .where(BattingScorecardSchema.innings_id == innings_id, BattingScorecardSchema.player_id == player_id)
        )
        card = result.scalar_one_or_none()
        if not card:
            card = BattingScorecardSchema(innings_id=innings_id, player_id=player_id, batting_position=position)
            session.add(card)
            await session.flush()
        return card

    @staticmethod
    async def get_batting_cards_for_players(
        session: AsyncSession, innings_id: int, player_ids: list
    ) -> dict:
        """Bulk-load batting cards for a set of players in ONE query (scoring hot path); returns {player_id: card_or_None}."""
        ids = [p for p in player_ids if p]
        if not ids:
            return {}
        res = await session.execute(
            select(BattingScorecardSchema)
            .where(
                BattingScorecardSchema.innings_id == innings_id,
                BattingScorecardSchema.player_id.in_(ids),
            )
        )
        found = {c.player_id: c for c in res.scalars().all()}
        return {pid: found.get(pid) for pid in ids}

    @staticmethod
    async def get_bowling_cards_for_players(
        session: AsyncSession, innings_id: int, player_ids: list
    ) -> dict:
        """Bulk-load bowling cards — same contract as get_batting_cards_for_players."""
        ids = [p for p in player_ids if p]
        if not ids:
            return {}
        res = await session.execute(
            select(BowlingScorecardSchema)
            .where(
                BowlingScorecardSchema.innings_id == innings_id,
                BowlingScorecardSchema.player_id.in_(ids),
            )
        )
        found = {c.player_id: c for c in res.scalars().all()}
        return {pid: found.get(pid) for pid in ids}

    @staticmethod
    async def ensure_batting_card(
        session: AsyncSession, innings_id: int, player_id: int,
        existing: BattingScorecardSchema | None, position: int | None = None,
    ) -> BattingScorecardSchema:
        """Use a card you already fetched in bulk, or create one if missing (no DB round-trip when existing is provided)."""
        if existing is not None:
            return existing
        card = BattingScorecardSchema(innings_id=innings_id, player_id=player_id, batting_position=position)
        session.add(card)
        await session.flush()
        return card

    @staticmethod
    async def ensure_bowling_card(
        session: AsyncSession, innings_id: int, player_id: int,
        existing: BowlingScorecardSchema | None,
    ) -> BowlingScorecardSchema:
        if existing is not None:
            return existing
        card = BowlingScorecardSchema(innings_id=innings_id, player_id=player_id)
        session.add(card)
        await session.flush()
        return card

    @staticmethod
    async def get_batting_by_innings(session: AsyncSession, innings_id: int) -> list:
        result = await session.execute(
            select(BattingScorecardSchema)
            .where(BattingScorecardSchema.innings_id == innings_id)
            .order_by(BattingScorecardSchema.batting_position)
        )
        return result.scalars().all()

    @staticmethod
    async def get_batting_for_innings_ids(session: AsyncSession, innings_ids: list) -> list:
        """Bulk-load batting cards for many innings in one query (replaces N per-innings calls in scorecard serialization)."""
        if not innings_ids:
            return []
        result = await session.execute(
            select(BattingScorecardSchema)
            .where(BattingScorecardSchema.innings_id.in_(innings_ids))
            .order_by(BattingScorecardSchema.innings_id, BattingScorecardSchema.batting_position)
        )
        return result.scalars().all()

    @staticmethod
    async def update_batting(session: AsyncSession, card: BattingScorecardSchema, data: dict):
        for key, value in data.items():
            setattr(card, key, value)
        await session.flush()

    @staticmethod
    async def get_or_create_bowling(session: AsyncSession, innings_id: int, player_id: int) -> BowlingScorecardSchema:
        result = await session.execute(
            select(BowlingScorecardSchema)
            .where(BowlingScorecardSchema.innings_id == innings_id, BowlingScorecardSchema.player_id == player_id)
        )
        card = result.scalar_one_or_none()
        if not card:
            card = BowlingScorecardSchema(innings_id=innings_id, player_id=player_id)
            session.add(card)
            await session.flush()
        return card

    @staticmethod
    async def get_bowling_by_innings(session: AsyncSession, innings_id: int) -> list:
        result = await session.execute(
            select(BowlingScorecardSchema)
            .where(BowlingScorecardSchema.innings_id == innings_id)
            .order_by(BowlingScorecardSchema.id)
        )
        return result.scalars().all()

    @staticmethod
    async def get_bowling_for_innings_ids(session: AsyncSession, innings_ids: list) -> list:
        if not innings_ids:
            return []
        result = await session.execute(
            select(BowlingScorecardSchema)
            .where(BowlingScorecardSchema.innings_id.in_(innings_ids))
            .order_by(BowlingScorecardSchema.innings_id, BowlingScorecardSchema.id)
        )
        return result.scalars().all()

    @staticmethod
    async def add_fall_of_wicket(session: AsyncSession, data: dict) -> FallOfWicketSchema:
        fow = FallOfWicketSchema(**data)
        session.add(fow)
        await session.flush()
        return fow

    @staticmethod
    async def get_fall_of_wickets(session: AsyncSession, innings_id: int) -> list:
        result = await session.execute(
            select(FallOfWicketSchema)
            .where(FallOfWicketSchema.innings_id == innings_id)
            .order_by(FallOfWicketSchema.wicket_number)
        )
        return result.scalars().all()

    @staticmethod
    async def get_fall_of_wickets_for_innings_ids(session: AsyncSession, innings_ids: list) -> list:
        if not innings_ids:
            return []
        result = await session.execute(
            select(FallOfWicketSchema)
            .where(FallOfWicketSchema.innings_id.in_(innings_ids))
            .order_by(FallOfWicketSchema.innings_id, FallOfWicketSchema.wicket_number)
        )
        return result.scalars().all()

    @staticmethod
    async def get_or_create_partnership(session: AsyncSession, innings_id: int, wicket_number: int, player_a_id: int, player_b_id: int) -> PartnershipSchema:
        result = await session.execute(
            select(PartnershipSchema)
            .where(PartnershipSchema.innings_id == innings_id, PartnershipSchema.is_active == True)
        )
        partnership = result.scalar_one_or_none()
        if not partnership:
            partnership = PartnershipSchema(
                innings_id=innings_id, wicket_number=wicket_number,
                player_a_id=player_a_id, player_b_id=player_b_id,
            )
            session.add(partnership)
            await session.flush()
        return partnership

    @staticmethod
    async def get_active_partnership(session: AsyncSession, innings_id: int) -> PartnershipSchema | None:
        result = await session.execute(
            select(PartnershipSchema)
            .where(PartnershipSchema.innings_id == innings_id, PartnershipSchema.is_active == True)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_partnerships(session: AsyncSession, innings_id: int) -> list:
        result = await session.execute(
            select(PartnershipSchema)
            .where(PartnershipSchema.innings_id == innings_id)
            .order_by(PartnershipSchema.wicket_number)
        )
        return result.scalars().all()

    @staticmethod
    async def get_partnerships_for_innings_ids(session: AsyncSession, innings_ids: list) -> list:
        if not innings_ids:
            return []
        result = await session.execute(
            select(PartnershipSchema)
            .where(PartnershipSchema.innings_id.in_(innings_ids))
            .order_by(PartnershipSchema.innings_id, PartnershipSchema.wicket_number)
        )
        return result.scalars().all()

    @staticmethod
    async def remove_fall_of_wicket_by_delivery(session: AsyncSession, innings_id: int, delivery_id: int):
        result = await session.execute(
            select(FallOfWicketSchema)
            .where(FallOfWicketSchema.innings_id == innings_id, FallOfWicketSchema.delivery_id == delivery_id)
        )
        fow = result.scalar_one_or_none()
        if fow:
            await session.delete(fow)
            await session.flush()

    @staticmethod
    async def revert_partnership_for_wicket(session: AsyncSession, innings_id: int):
        """Delete the newest partnership (created after wicket) and reactivate the previous one."""
        result = await session.execute(
            select(PartnershipSchema)
            .where(PartnershipSchema.innings_id == innings_id)
            .order_by(PartnershipSchema.wicket_number.desc())
            .limit(2)
        )
        partnerships = result.scalars().all()
        if len(partnerships) >= 2:
            # Delete newest, reactivate previous
            await session.delete(partnerships[0])
            partnerships[1].is_active = True
            await session.flush()
        elif len(partnerships) == 1:
            # Only one partnership, reactivate it
            partnerships[0].is_active = True
            await session.flush()
