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
        """List matches with optional filters.

        `for_user` (new): fetches matches where the user CREATED or PLAYED in
        (i.e. their player profile appears in match_squads). Each returned
        match gets a `.role` attribute: 'organized' | 'played' | 'both'.
        This replaces the old `created_by`-only filter for "My Matches".

        When `for_user` is set, `created_by` is ignored.
        """
        from src.database.postgres.schemas.team_schema import TeamSchema as TS
        from sqlalchemy.orm import load_only
        from sqlalchemy import or_, case, and_, literal_column

        if for_user:
            # Single query: matches created by user OR where user's player is in squad.
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

        # Standard path (no for_user)
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
    async def get_batting_aggregates_by_tournament(session: AsyncSession, tournament_id: int) -> list:
        """Per-player batting totals across all completed matches in a tournament.

        Single GROUP BY query — replaces fetching every batting_scorecard row
        and aggregating in Python. Returns: (player_id, full_name, matches,
        innings, total_runs, total_balls, total_fours, total_sixes,
        highest_score). Sorted by total runs desc.
        """
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
        """Top individual batting innings (for the 'highest scores' panel).
        Sorted by runs desc; only fetches `limit` rows. Replaces the in-memory
        sort over every batting row in the tournament.
        """
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
        """Per-player bowling totals across all completed matches.

        Single GROUP BY query — replaces fetching every bowling_scorecard row
        and aggregating in Python. Returns: (player_id, full_name, matches,
        innings, total_wickets, total_runs_conceded, total_overs, total_maidens,
        total_dot_balls). Sorted by wickets desc.
        """
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
        """Per-player best bowling figures (max wickets, tiebreak min runs).

        Uses Postgres `DISTINCT ON` so each player gets one row — the row
        with their best single-innings figures. Joins back to the aggregate
        result in Python via player_id.
        """
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
