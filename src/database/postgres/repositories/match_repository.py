import secrets
import string
from sqlalchemy import select, text, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.match_schema import MatchSchema
from src.database.postgres.schemas.match_squad_schema import MatchSquadSchema
from src.database.postgres.schemas.player_schema import PlayerSchema
from src.database.postgres.schemas.innings_schema import InningsSchema
from src.database.postgres.schemas.batting_scorecard_schema import BattingScorecardSchema
from src.database.postgres.schemas.bowling_scorecard_schema import BowlingScorecardSchema

_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _generate_match_code() -> str:
    return "M" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


class MatchRepository:
    """Repositories flush, services commit — see InningsRepository for the pattern."""

    @staticmethod
    async def create(session: AsyncSession, data: dict) -> MatchSchema:
        # Relies on the DB UNIQUE constraint on matches.match_code: generate,
        # insert, and retry on IntegrityError instead of a TOCTOU pre-check.
        caller_supplied_code = bool(data.get("match_code"))
        for _ in range(10):
            if not caller_supplied_code:
                data["match_code"] = _generate_match_code()
            match = MatchSchema(**data)
            session.add(match)
            try:
                await session.flush()
                return match
            except IntegrityError:
                await session.rollback()
                if caller_supplied_code:
                    # Caller picked a colliding code — not something we can retry around.
                    raise
                # else: generated code collided; loop picks a new one.
        raise RuntimeError("Could not generate a unique match_code after 10 attempts")

    @staticmethod
    async def get_by_id(session: AsyncSession, match_id: int) -> MatchSchema | None:
        result = await session.execute(select(MatchSchema).where(MatchSchema.id == match_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_code(session: AsyncSession, code: str) -> MatchSchema | None:
        result = await session.execute(select(MatchSchema).where(MatchSchema.match_code == code))
        return result.scalar_one_or_none()

    # Columns loaded for every match list/detail view
    _LIST_COLS = [
        MatchSchema.id, MatchSchema.match_code, MatchSchema.status,
        MatchSchema.team_a_id, MatchSchema.team_b_id,
        MatchSchema.overs, MatchSchema.tournament_id,
        MatchSchema.match_date, MatchSchema.result_summary,
        MatchSchema.winner_id, MatchSchema.created_by,
        MatchSchema.match_type, MatchSchema.time_slot,
        MatchSchema.stage_id, MatchSchema.group_id, MatchSchema.match_number,
        MatchSchema.venue_id, MatchSchema.current_innings, MatchSchema.created_at,
    ]

    @staticmethod
    async def get_all(
        session: AsyncSession, status: str = None, tournament_id: int = None,
        search: str = None, created_by: int = None, stage_id: int = None,
        for_user: int = None, role: str = None,
        limit: int = 50, offset: int = 0,
    ) -> list:
        """List matches with optional filters; when `for_user` is set, fetches matches the user CREATED or PLAYED in (each row gets a `.role` attribute: organized|played|both) and `created_by` is ignored."""
        from src.database.postgres.schemas.team_schema import TeamSchema as TS
        from sqlalchemy.orm import load_only
        from sqlalchemy import or_, case, and_, literal_column

        if for_user:
            # Uses EXISTS subquery for "played" check — fast with indexes on
            # match_squads(match_id) and players(user_id).
            from src.database.postgres.schemas.player_schema import PlayerSchema
            played_exists = (
                select(MatchSquadSchema.id)
                .join(PlayerSchema, MatchSquadSchema.player_id == PlayerSchema.id)
                .where(
                    MatchSquadSchema.match_id == MatchSchema.id,
                    PlayerSchema.user_id == for_user,
                )
                .correlate(MatchSchema)
                .exists()
            )
            is_organizer = MatchSchema.created_by == for_user
            role_expr = case(
                (and_(is_organizer, played_exists), literal_column("'both'")),
                (is_organizer, literal_column("'organized'")),
                else_=literal_column("'played'"),
            ).label('role')

            # Apply role filter server-side (before LIMIT/OFFSET) for correct pagination
            if role == 'played':
                where_clause = played_exists
            elif role == 'organized':
                where_clause = is_organizer
            elif role == 'both':
                where_clause = and_(is_organizer, played_exists)
            else:
                where_clause = or_(is_organizer, played_exists)

            query = (
                select(MatchSchema, role_expr)
                .options(load_only(*MatchRepository._LIST_COLS))
                .where(where_clause)
            )
            if status:
                query = query.where(MatchSchema.status == status)
            if tournament_id:
                query = query.where(MatchSchema.tournament_id == tournament_id)
            if stage_id is not None:
                query = query.where(MatchSchema.stage_id == stage_id)
            if search:
                team_a = select(TS.id).where(TS.name.ilike(f"%{search}%"), TS.id == MatchSchema.team_a_id).correlate(MatchSchema).exists()
                team_b = select(TS.id).where(TS.name.ilike(f"%{search}%"), TS.id == MatchSchema.team_b_id).correlate(MatchSchema).exists()
                code_match = MatchSchema.match_code.ilike(f"%{search}%")
                query = query.where(or_(team_a, team_b, code_match))
            query = query.order_by(MatchSchema.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(query)
            matches = []
            for row in result.all():
                m = row[0]
                m._role = row[1]  # attach role as a transient attribute
                matches.append(m)
            return matches

        query = select(MatchSchema).options(load_only(*MatchRepository._LIST_COLS))
        if status:
            query = query.where(MatchSchema.status == status)
        if tournament_id:
            query = query.where(MatchSchema.tournament_id == tournament_id)
        if stage_id is not None:
            query = query.where(MatchSchema.stage_id == stage_id)
        if created_by:
            query = query.where(MatchSchema.created_by == created_by)
        if search:
            team_a = select(TS.id).where(TS.name.ilike(f"%{search}%"), TS.id == MatchSchema.team_a_id).correlate(MatchSchema).exists()
            team_b = select(TS.id).where(TS.name.ilike(f"%{search}%"), TS.id == MatchSchema.team_b_id).correlate(MatchSchema).exists()
            code_match = MatchSchema.match_code.ilike(f"%{search}%")
            query = query.where(or_(team_a, team_b, code_match))
        query = query.order_by(MatchSchema.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def update(session: AsyncSession, match_id: int, data: dict) -> MatchSchema | None:
        """Update match attributes; None values are skipped (uses session.get() so returned instance is ORM-attached)."""
        filtered = {k: v for k, v in data.items() if v is not None}
        if not filtered:
            return None
        match = await session.get(MatchSchema, match_id)
        if match is None:
            return None
        for k, v in filtered.items():
            setattr(match, k, v)
        await session.flush()
        return match

    @staticmethod
    async def set_squad(session: AsyncSession, entries: list[dict]) -> list:
        if not entries:
            return []
        match_id = entries[0]["match_id"]
        team_id = entries[0]["team_id"]
        # Single round-trip: delete old rows. add_all inserts the new ones in a batch.
        await session.execute(
            delete(MatchSquadSchema).where(
                MatchSquadSchema.match_id == match_id,
                MatchSquadSchema.team_id == team_id,
            )
        )
        squads = [MatchSquadSchema(**entry) for entry in entries]
        session.add_all(squads)
        await session.flush()
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
    async def get_batting_aggregates_by_tournament(session: AsyncSession, tournament_id: int) -> list:
        """Per-player batting totals across all completed matches — single GROUP BY replaces per-row aggregation in Python."""
        from sqlalchemy import func as sa_func, distinct
        result = await session.execute(
            select(
                PlayerSchema.id.label("player_id"),
                PlayerSchema.full_name,
                sa_func.count(distinct(InningsSchema.match_id)).label("matches"),
                sa_func.count(BattingScorecardSchema.id).label("innings"),
                sa_func.coalesce(sa_func.sum(BattingScorecardSchema.runs), 0).label("total_runs"),
                sa_func.coalesce(sa_func.sum(BattingScorecardSchema.balls_faced), 0).label("total_balls"),
                sa_func.coalesce(sa_func.sum(BattingScorecardSchema.fours), 0).label("total_fours"),
                sa_func.coalesce(sa_func.sum(BattingScorecardSchema.sixes), 0).label("total_sixes"),
                sa_func.coalesce(sa_func.max(BattingScorecardSchema.runs), 0).label("highest_score"),
            )
            .join(InningsSchema, BattingScorecardSchema.innings_id == InningsSchema.id)
            .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
            .join(PlayerSchema, BattingScorecardSchema.player_id == PlayerSchema.id)
            .where(MatchSchema.tournament_id == tournament_id, MatchSchema.status == "completed")
            .group_by(PlayerSchema.id, PlayerSchema.full_name)
            .order_by(sa_func.sum(BattingScorecardSchema.runs).desc().nullslast())
        )
        return result.all()

    @staticmethod
    async def get_top_batting_innings_by_tournament(
        session: AsyncSession, tournament_id: int, limit: int = 20
    ) -> list:
        """Top individual batting innings sorted by runs desc; fetches only `limit` rows instead of in-memory sort over every row."""
        result = await session.execute(
            select(
                PlayerSchema.id.label("player_id"),
                PlayerSchema.full_name,
                BattingScorecardSchema.runs,
                BattingScorecardSchema.balls_faced,
                BattingScorecardSchema.fours,
                BattingScorecardSchema.sixes,
                InningsSchema.match_id,
            )
            .join(InningsSchema, BattingScorecardSchema.innings_id == InningsSchema.id)
            .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
            .join(PlayerSchema, BattingScorecardSchema.player_id == PlayerSchema.id)
            .where(MatchSchema.tournament_id == tournament_id, MatchSchema.status == "completed")
            .order_by(BattingScorecardSchema.runs.desc().nullslast())
            .limit(limit)
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
    async def get_bowling_aggregates_by_tournament(session: AsyncSession, tournament_id: int) -> list:
        """Per-player bowling totals across all completed matches — single GROUP BY replaces per-row aggregation in Python."""
        from sqlalchemy import func as sa_func, distinct
        result = await session.execute(
            select(
                PlayerSchema.id.label("player_id"),
                PlayerSchema.full_name,
                sa_func.count(distinct(InningsSchema.match_id)).label("matches"),
                sa_func.count(BowlingScorecardSchema.id).label("innings"),
                sa_func.coalesce(sa_func.sum(BowlingScorecardSchema.wickets), 0).label("total_wickets"),
                sa_func.coalesce(sa_func.sum(BowlingScorecardSchema.runs_conceded), 0).label("total_runs_conceded"),
                sa_func.coalesce(sa_func.sum(BowlingScorecardSchema.overs_bowled), 0.0).label("total_overs"),
                sa_func.coalesce(sa_func.sum(BowlingScorecardSchema.maidens), 0).label("total_maidens"),
                sa_func.coalesce(sa_func.sum(BowlingScorecardSchema.dot_balls), 0).label("total_dot_balls"),
            )
            .join(InningsSchema, BowlingScorecardSchema.innings_id == InningsSchema.id)
            .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
            .join(PlayerSchema, BowlingScorecardSchema.player_id == PlayerSchema.id)
            .where(MatchSchema.tournament_id == tournament_id, MatchSchema.status == "completed")
            .group_by(PlayerSchema.id, PlayerSchema.full_name)
            .order_by(sa_func.sum(BowlingScorecardSchema.wickets).desc().nullslast())
        )
        return result.all()

    @staticmethod
    async def get_best_bowling_figures_by_tournament(
        session: AsyncSession, tournament_id: int
    ) -> list:
        """Per-player best bowling figures via Postgres DISTINCT ON (max wickets, tiebreak min runs); joined back to aggregates by player_id."""
        result = await session.execute(
            select(
                BowlingScorecardSchema.player_id,
                BowlingScorecardSchema.wickets,
                BowlingScorecardSchema.runs_conceded,
            )
            .distinct(BowlingScorecardSchema.player_id)
            .join(InningsSchema, BowlingScorecardSchema.innings_id == InningsSchema.id)
            .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
            .where(MatchSchema.tournament_id == tournament_id, MatchSchema.status == "completed")
            .order_by(
                BowlingScorecardSchema.player_id,
                BowlingScorecardSchema.wickets.desc().nullslast(),
                BowlingScorecardSchema.runs_conceded.asc().nullslast(),
            )
        )
        return result.all()
