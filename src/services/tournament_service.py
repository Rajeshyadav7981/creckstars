import random
import string
from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.tournament_repository import TournamentRepository
from src.database.postgres.repositories.team_repository import TeamRepository
from src.database.postgres.repositories.match_repository import MatchRepository


def _generate_code(prefix: str = "T") -> str:
    chars = string.ascii_uppercase + string.digits
    return prefix + "".join(random.choices(chars, k=6))


class TournamentService:

    @staticmethod
    async def create_tournament(session: AsyncSession, user_id: int, **kwargs):
        kwargs["created_by"] = user_id
        for _ in range(10):
            code = _generate_code("T")
            existing = await TournamentRepository.get_by_code(session, code)
            if not existing:
                kwargs["tournament_code"] = code
                break
        return await TournamentRepository.create(session, kwargs)

    @staticmethod
    async def get_tournament(session: AsyncSession, tournament_id: int):
        tournament = await TournamentRepository.get_by_id(session, tournament_id)
        if not tournament:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")
        return tournament

    @staticmethod
    async def get_tournaments(
        session: AsyncSession, status_filter: str = None, created_by: int = None,
        search: str = None, for_user: int = None, role: str = None,
        limit: int = 50, offset: int = 0,
    ):
        return await TournamentRepository.get_all(
            session, status=status_filter, created_by=created_by,
            search=search, for_user=for_user, role=role,
            limit=limit, offset=offset,
        )

    @staticmethod
    async def get_tournament_detail(session: AsyncSession, tournament_id: int):
        tournament = await TournamentRepository.get_by_id(session, tournament_id)
        if not tournament:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")
        teams = await TournamentRepository.get_teams(session, tournament_id)
        matches = await MatchRepository.get_all(session, tournament_id=tournament_id)

        # Batch load all innings for tournament matches (1 query instead of N)
        from src.database.postgres.schemas.innings_schema import InningsSchema
        from collections import defaultdict
        innings_by_match = defaultdict(list)
        if matches:
            match_ids = [m.id for m in matches]
            from sqlalchemy import select as sa_select
            innings_result = await session.execute(
                sa_select(InningsSchema)
                .where(InningsSchema.match_id.in_(match_ids))
                .order_by(InningsSchema.match_id, InningsSchema.innings_number)
            )
            for inn in innings_result.scalars().all():
                innings_by_match[inn.match_id].append(inn)

        # Batch load all stages and groups (2 queries instead of N+1)
        from src.database.postgres.repositories.tournament_stage_repository import TournamentStageRepository
        from src.database.postgres.schemas.tournament_group_schema import TournamentGroupSchema
        stage_map = {}
        group_map = {}
        try:
            stages = await TournamentStageRepository.get_stages(session, tournament_id)
            stage_ids = []
            for s in stages:
                stage_map[s.id] = s.stage_name
                stage_ids.append(s.id)
            if stage_ids:
                groups_result = await session.execute(
                    sa_select(TournamentGroupSchema)
                    .where(TournamentGroupSchema.stage_id.in_(stage_ids))
                )
                for g in groups_result.scalars().all():
                    group_map[g.id] = g.group_name
        except Exception as _e:
            pass  # logged below not to crash hot path

        return {
            "tournament": tournament,
            "teams": teams,
            "matches": matches,
            "innings_by_match": innings_by_match,
            "stage_map": stage_map,
            "group_map": group_map,
        }

    @staticmethod
    def _check_owner(entity, user_id: int):
        if entity.created_by != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the creator can perform this action")

    @staticmethod
    async def add_team(session: AsyncSession, tournament_id: int, team_id: int, user_id: int = None):
        tournament = await TournamentRepository.get_by_id(session, tournament_id)
        if not tournament:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")
        if user_id:
            TournamentService._check_owner(tournament, user_id)
        team = await TeamRepository.get_by_id(session, team_id)
        if not team:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        try:
            return await TournamentRepository.add_team(session, tournament_id, team_id)
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Team already in tournament")

    @staticmethod
    async def remove_team(session: AsyncSession, tournament_id: int, team_id: int, user_id: int = None):
        if user_id:
            tournament = await TournamentRepository.get_by_id(session, tournament_id)
            if not tournament:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")
            TournamentService._check_owner(tournament, user_id)
        removed = await TournamentRepository.remove_team(session, tournament_id, team_id)
        if not removed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not in tournament")
        return {"message": "Team removed from tournament"}

    @staticmethod
    async def get_standings(session: AsyncSession, tournament_id: int):
        tournament = await TournamentRepository.get_by_id(session, tournament_id)
        if not tournament:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")

        teams = await TournamentRepository.get_teams(session, tournament_id)
        matches = await MatchRepository.get_completed_by_tournament(session, tournament_id)
        innings_list = await MatchRepository.get_innings_by_tournament(session, tournament_id)

        # Build team lookup
        team_map = {t.id: t for t in teams}

        standings = {}
        for t in teams:
            standings[t.id] = {
                "team_id": t.id,
                "team_name": t.name,
                "short_name": t.short_name,
                "played": 0,
                "won": 0,
                "lost": 0,
                "drawn": 0,
                "points": 0,
                "runs_scored": 0,
                "overs_faced": 0.0,
                "runs_conceded": 0,
                "overs_bowled": 0.0,
                "nrr": 0.0,
            }

        # Build innings lookup by match_id
        innings_by_match = {}
        for inn in innings_list:
            innings_by_match.setdefault(inn.match_id, []).append(inn)

        # Get configurable points from tournament
        pts_win = tournament.points_per_win if hasattr(tournament, 'points_per_win') and tournament.points_per_win else 2
        pts_draw = tournament.points_per_draw if hasattr(tournament, 'points_per_draw') and tournament.points_per_draw else 1
        pts_nr = tournament.points_per_no_result if hasattr(tournament, 'points_per_no_result') and tournament.points_per_no_result else 0

        # Process each completed match
        for match in matches:
            team_a = match.team_a_id
            team_b = match.team_b_id
            if team_a not in standings or team_b not in standings:
                continue

            rt = getattr(match, 'result_type', None) or 'normal'

            # No result / abandoned — each team gets no_result points
            if rt in ('no_result', 'abandoned'):
                standings[team_a]["played"] += 1
                standings[team_b]["played"] += 1
                standings[team_a]["no_result"] = standings[team_a].get("no_result", 0) + 1
                standings[team_b]["no_result"] = standings[team_b].get("no_result", 0) + 1
                standings[team_a]["points"] += pts_nr
                standings[team_b]["points"] += pts_nr
                continue  # Skip NRR accumulation for NR/abandoned

            standings[team_a]["played"] += 1
            standings[team_b]["played"] += 1

            if match.winner_id:
                if match.winner_id == team_a:
                    standings[team_a]["won"] += 1
                    standings[team_a]["points"] += pts_win
                    standings[team_b]["lost"] += 1
                elif match.winner_id == team_b:
                    standings[team_b]["won"] += 1
                    standings[team_b]["points"] += pts_win
                    standings[team_a]["lost"] += 1
            else:
                # Tied / draw
                standings[team_a]["drawn"] += 1
                standings[team_b]["drawn"] += 1
                standings[team_a]["points"] += pts_draw
                standings[team_b]["points"] += pts_draw

            # Skip NRR accumulation for walkovers/forfeits (no innings played)
            if rt in ('walkover', 'forfeit', 'awarded'):
                continue

            # Accumulate runs and overs from innings
            match_innings = innings_by_match.get(match.id, [])
            for inn in match_innings:
                bat_team = inn.batting_team_id
                bowl_team = inn.bowling_team_id
                if bat_team in standings:
                    standings[bat_team]["runs_scored"] += inn.total_runs or 0
                    standings[bat_team]["overs_faced"] += inn.total_overs or 0.0
                if bowl_team in standings:
                    standings[bowl_team]["runs_conceded"] += inn.total_runs or 0
                    standings[bowl_team]["overs_bowled"] += inn.total_overs or 0.0

        # Calculate NRR for each team
        for s in standings.values():
            run_rate_for = (s["runs_scored"] / s["overs_faced"]) if s["overs_faced"] > 0 else 0.0
            run_rate_against = (s["runs_conceded"] / s["overs_bowled"]) if s["overs_bowled"] > 0 else 0.0
            s["nrr"] = round(run_rate_for - run_rate_against, 3)

        # Sort by points desc, then NRR desc
        sorted_standings = sorted(
            standings.values(),
            key=lambda x: (x["points"], x["nrr"]),
            reverse=True,
        )

        # Remove intermediate fields from response
        for s in sorted_standings:
            del s["runs_scored"]
            del s["overs_faced"]
            del s["runs_conceded"]
            del s["overs_bowled"]

        return sorted_standings

    @staticmethod
    async def get_leaderboard(session: AsyncSession, tournament_id: int):
        tournament = await TournamentRepository.get_by_id(session, tournament_id)
        if not tournament:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")

        # Pull all aggregates from Postgres in 4 cheap GROUP BY / ORDER BY
        # queries instead of fetching every scorecard row and looping in
        # Python. Same output shape — pure perf change.
        bat_aggs = await MatchRepository.get_batting_aggregates_by_tournament(session, tournament_id)
        top_innings = await MatchRepository.get_top_batting_innings_by_tournament(session, tournament_id, limit=20)
        bowl_aggs = await MatchRepository.get_bowling_aggregates_by_tournament(session, tournament_id)
        best_figures = await MatchRepository.get_best_bowling_figures_by_tournament(session, tournament_id)

        # Batting list — already sorted by total runs desc in SQL
        batsmen_list = []
        for r in bat_aggs:
            innings = int(r.innings or 0)
            runs = int(r.total_runs or 0)
            balls = int(r.total_balls or 0)
            avg = round(runs / innings, 2) if innings > 0 else 0.0
            sr = round((runs / balls) * 100, 2) if balls > 0 else 0.0
            batsmen_list.append({
                "player_id": r.player_id,
                "player_name": r.full_name,
                "matches": int(r.matches or 0),
                "innings": innings,
                "runs": runs,
                "balls_faced": balls,
                "fours": int(r.total_fours or 0),
                "sixes": int(r.total_sixes or 0),
                "highest_score": int(r.highest_score or 0),
                "average": avg,
                "strike_rate": sr,
            })

        # Top individual innings — already sorted + limited in SQL
        highest_scores = [{
            "player_id": r.player_id,
            "player_name": r.full_name,
            "runs": int(r.runs or 0),
            "balls_faced": int(r.balls_faced or 0),
            "fours": int(r.fours or 0),
            "sixes": int(r.sixes or 0),
            "match_id": r.match_id,
        } for r in top_innings]

        # Index best bowling figures by player_id for the join below
        best_by_player = {
            r.player_id: (int(r.wickets or 0), int(r.runs_conceded or 0))
            for r in best_figures
        }

        # Bowling list — already sorted by wickets desc in SQL
        bowlers_list = []
        for r in bowl_aggs:
            wickets = int(r.total_wickets or 0)
            runs = int(r.total_runs_conceded or 0)
            overs = float(r.total_overs or 0.0)
            economy = round(runs / overs, 2) if overs > 0 else 0.0
            avg = round(runs / wickets, 2) if wickets > 0 else 0.0
            best_w, best_r = best_by_player.get(r.player_id, (0, 0))
            bowlers_list.append({
                "player_id": r.player_id,
                "player_name": r.full_name,
                "matches": int(r.matches or 0),
                "innings": int(r.innings or 0),
                "wickets": wickets,
                "runs_conceded": runs,
                "overs": overs,
                "maidens": int(r.total_maidens or 0),
                "dot_balls": int(r.total_dot_balls or 0),
                "economy": economy,
                "average": avg,
                "best": f"{best_w}/{best_r}",
            })

        # Aggregate fielding stats (catches, run outs, stumpings) — single
        # GROUP BY query with conditional SUMs and ORDER BY total desc.
        # Replaces the per-row dict-merge loop + Python sort.
        from sqlalchemy import case
        from src.database.postgres.schemas.delivery_schema import DeliverySchema
        from src.database.postgres.schemas.innings_schema import InningsSchema
        from src.database.postgres.schemas.match_schema import MatchSchema
        from src.database.postgres.schemas.player_schema import PlayerSchema

        catches_expr = func.sum(case((DeliverySchema.wicket_type == 'caught', 1), else_=0))
        run_outs_expr = func.sum(case((DeliverySchema.wicket_type == 'run_out', 1), else_=0))
        stumpings_expr = func.sum(case((DeliverySchema.wicket_type == 'stumped', 1), else_=0))
        total_expr = catches_expr + run_outs_expr + stumpings_expr

        fielding_rows = await session.execute(
            select(
                DeliverySchema.fielder_id.label("player_id"),
                PlayerSchema.full_name.label("name"),
                catches_expr.label("catches"),
                run_outs_expr.label("run_outs"),
                stumpings_expr.label("stumpings"),
                total_expr.label("total"),
            )
            .join(InningsSchema, DeliverySchema.innings_id == InningsSchema.id)
            .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
            .join(PlayerSchema, DeliverySchema.fielder_id == PlayerSchema.id)
            .where(
                MatchSchema.tournament_id == tournament_id,
                DeliverySchema.is_wicket == True,
                DeliverySchema.fielder_id.isnot(None),
            )
            .group_by(DeliverySchema.fielder_id, PlayerSchema.full_name)
            # "Best fielder" = most catches. Ties broken by total contributions
            # (run-outs + stumpings) so a pure fielder with 6 catches still beats
            # a keeper with 6 stumpings-only.
            .order_by(catches_expr.desc(), total_expr.desc())
        )
        fielders_list = [{
            "player_id": r.player_id,
            "name": r.name,
            "catches": int(r.catches or 0),
            "run_outs": int(r.run_outs or 0),
            "stumpings": int(r.stumpings or 0),
            "total": int(r.total or 0),
        } for r in fielding_rows.all()]

        return {
            "top_batsmen": batsmen_list[:20],
            "top_bowlers": bowlers_list[:20],
            "top_fielders": fielders_list[:20],
            "highest_scores": highest_scores[:20],
        }
