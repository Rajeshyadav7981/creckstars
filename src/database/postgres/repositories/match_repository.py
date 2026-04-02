from sqlalchemy import select, text, delete
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.match_schema import MatchSchema
from src.database.postgres.schemas.match_squad_schema import MatchSquadSchema
from src.database.postgres.schemas.player_schema import PlayerSchema
from src.database.postgres.schemas.innings_schema import InningsSchema
from src.database.postgres.schemas.batting_scorecard_schema import BattingScorecardSchema
from src.database.postgres.schemas.bowling_scorecard_schema import BowlingScorecardSchema


class MatchRepository:

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> MatchSchema:
        # Auto-generate match code if not provided
        if not data.get("match_code"):
            import random, string
            for _ in range(10):
                code = "M" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
                existing = await session.execute(
                    select(MatchSchema).where(MatchSchema.match_code == code)
                )
                if not existing.scalar_one_or_none():
                    data["match_code"] = code
                    break
        match = MatchSchema(**data)
        session.add(match)
        await session.commit()
        await session.refresh(match)
        return match

    @staticmethod
    async def get_by_id(session: AsyncSession, match_id: int) -> MatchSchema | None:
        result = await session.execute(select(MatchSchema).where(MatchSchema.id == match_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_code(session: AsyncSession, code: str) -> MatchSchema | None:
        result = await session.execute(select(MatchSchema).where(MatchSchema.match_code == code))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(
        session: AsyncSession, status: str = None, tournament_id: int = None,
        search: str = None, created_by: int = None, limit: int = 50, offset: int = 0,
    ) -> list:
        from src.database.postgres.schemas.team_schema import TeamSchema as TS
        from sqlalchemy.orm import load_only
        # Only load columns needed for list/detail view — skip heavy/unused ones
        query = select(MatchSchema).options(load_only(
            MatchSchema.id, MatchSchema.match_code, MatchSchema.status,
            MatchSchema.team_a_id, MatchSchema.team_b_id,
            MatchSchema.overs, MatchSchema.tournament_id,
            MatchSchema.match_date, MatchSchema.result_summary,
            MatchSchema.winner_id, MatchSchema.created_by,
            MatchSchema.match_type, MatchSchema.time_slot,
            MatchSchema.stage_id, MatchSchema.group_id, MatchSchema.match_number,
            MatchSchema.current_innings, MatchSchema.created_at,
        ))
        if status:
            query = query.where(MatchSchema.status == status)
        if tournament_id:
            query = query.where(MatchSchema.tournament_id == tournament_id)
        if created_by:
            query = query.where(MatchSchema.created_by == created_by)
        if search:
            # Search by team names or match code
            from sqlalchemy import or_, exists
            team_a = select(TS.id).where(TS.name.ilike(f"%{search}%"), TS.id == MatchSchema.team_a_id).correlate(MatchSchema).exists()
            team_b = select(TS.id).where(TS.name.ilike(f"%{search}%"), TS.id == MatchSchema.team_b_id).correlate(MatchSchema).exists()
            code_match = MatchSchema.match_code.ilike(f"%{search}%")
            query = query.where(or_(team_a, team_b, code_match))
        query = query.order_by(MatchSchema.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def update(session: AsyncSession, match_id: int, data: dict) -> MatchSchema | None:
        result = await session.execute(select(MatchSchema).where(MatchSchema.id == match_id))
        match = result.scalar_one_or_none()
        if not match:
            return None
        for key, value in data.items():
            if value is not None:
                setattr(match, key, value)
        await session.commit()
        await session.refresh(match)
        return match

    @staticmethod
    async def set_squad(session: AsyncSession, entries: list[dict]) -> list:
        if not entries:
            return []
        # Delete existing squad for this match+team first (allows re-selection)
        match_id = entries[0]["match_id"]
        team_id = entries[0]["team_id"]
        await session.execute(
            delete(MatchSquadSchema).where(
                MatchSquadSchema.match_id == match_id,
                MatchSquadSchema.team_id == team_id,
            )
        )
        await session.flush()
        # Insert fresh squad
        squads = []
        for entry in entries:
            sq = MatchSquadSchema(**entry)
            session.add(sq)
            squads.append(sq)
        await session.commit()
        for sq in squads:
            await session.refresh(sq)
        return squads

    @staticmethod
    async def get_squad(session: AsyncSession, match_id: int, team_id: int) -> list:
        result = await session.execute(
            select(PlayerSchema, MatchSquadSchema)
            .join(MatchSquadSchema, PlayerSchema.id == MatchSquadSchema.player_id)
            .where(MatchSquadSchema.match_id == match_id, MatchSquadSchema.team_id == team_id)
            .order_by(MatchSquadSchema.batting_order)
        )
        return result.all()

    @staticmethod
    async def get_completed_by_tournament(session: AsyncSession, tournament_id: int) -> list:
        from sqlalchemy.orm import load_only
        # Standings only needs: team_a_id, team_b_id, winner_id, result_type
        result = await session.execute(
            select(MatchSchema).options(load_only(
                MatchSchema.id, MatchSchema.team_a_id, MatchSchema.team_b_id,
                MatchSchema.winner_id, MatchSchema.result_type, MatchSchema.status,
            ))
            .where(MatchSchema.tournament_id == tournament_id, MatchSchema.status == "completed")
            .order_by(MatchSchema.created_at)
        )
        return result.scalars().all()

    @staticmethod
    async def get_innings_by_tournament(session: AsyncSession, tournament_id: int) -> list:
        from sqlalchemy.orm import load_only
        # Standings only needs: match_id, batting_team_id, bowling_team_id, total_runs, total_overs
        result = await session.execute(
            select(InningsSchema).options(load_only(
                InningsSchema.match_id, InningsSchema.batting_team_id,
                InningsSchema.bowling_team_id, InningsSchema.total_runs,
                InningsSchema.total_overs,
            ))
            .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
            .where(MatchSchema.tournament_id == tournament_id, MatchSchema.status == "completed")
            .order_by(InningsSchema.match_id, InningsSchema.innings_number)
        )
        return result.scalars().all()

    @staticmethod
    async def get_batting_scorecards_by_tournament(session: AsyncSession, tournament_id: int) -> list:
        result = await session.execute(
            select(BattingScorecardSchema, PlayerSchema, InningsSchema)
            .join(InningsSchema, BattingScorecardSchema.innings_id == InningsSchema.id)
            .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
            .join(PlayerSchema, BattingScorecardSchema.player_id == PlayerSchema.id)
            .where(MatchSchema.tournament_id == tournament_id, MatchSchema.status == "completed")
        )
        return result.all()

    @staticmethod
    async def get_nearby(session: AsyncSession, lat: float, lng: float, radius: float) -> list:
        """Find matches at venues within a given radius (km) using Haversine formula."""
        query = text("""
            SELECT m.id, m.match_code, m.status, m.team_a_id, m.team_b_id,
                   m.overs, m.match_date, m.tournament_id, m.venue_id,
                   v.name as venue_name, v.latitude, v.longitude,
                   ta.name as team_a_name, tb.name as team_b_name,
                   (6371 * acos(
                       LEAST(1.0, cos(radians(:lat)) * cos(radians(v.latitude))
                       * cos(radians(v.longitude) - radians(:lng))
                       + sin(radians(:lat)) * sin(radians(v.latitude)))
                   )) AS distance_km
            FROM matches m
            JOIN venues v ON m.venue_id = v.id
            JOIN teams ta ON m.team_a_id = ta.id
            JOIN teams tb ON m.team_b_id = tb.id
            WHERE v.latitude IS NOT NULL AND v.longitude IS NOT NULL
            AND (6371 * acos(
                LEAST(1.0, cos(radians(:lat)) * cos(radians(v.latitude))
                * cos(radians(v.longitude) - radians(:lng))
                + sin(radians(:lat)) * sin(radians(v.latitude)))
            )) <= :radius
            ORDER BY distance_km, m.match_date
            LIMIT 50
        """)
        result = await session.execute(query, {"lat": lat, "lng": lng, "radius": radius})
        return result.mappings().all()

    @staticmethod
    async def get_bowling_scorecards_by_tournament(session: AsyncSession, tournament_id: int) -> list:
        result = await session.execute(
            select(BowlingScorecardSchema, PlayerSchema, InningsSchema)
            .join(InningsSchema, BowlingScorecardSchema.innings_id == InningsSchema.id)
            .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
            .join(PlayerSchema, BowlingScorecardSchema.player_id == PlayerSchema.id)
            .where(MatchSchema.tournament_id == tournament_id, MatchSchema.status == "completed")
        )
        return result.all()
