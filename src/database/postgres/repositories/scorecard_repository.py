from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.batting_scorecard_schema import BattingScorecardSchema
from src.database.postgres.schemas.bowling_scorecard_schema import BowlingScorecardSchema
from src.database.postgres.schemas.fall_of_wicket_schema import FallOfWicketSchema
from src.database.postgres.schemas.partnership_schema import PartnershipSchema


class ScorecardRepository:

    # --- Batting ---
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
    async def get_batting_by_innings(session: AsyncSession, innings_id: int) -> list:
        result = await session.execute(
            select(BattingScorecardSchema)
            .where(BattingScorecardSchema.innings_id == innings_id)
            .order_by(BattingScorecardSchema.batting_position)
        )
        return result.scalars().all()

    @staticmethod
    async def update_batting(session: AsyncSession, card: BattingScorecardSchema, data: dict):
        for key, value in data.items():
            setattr(card, key, value)
        await session.flush()

    # --- Bowling ---
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

    # --- Fall of Wickets ---
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

    # --- Partnerships ---
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
