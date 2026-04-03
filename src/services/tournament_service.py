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
        search: str = None, limit: int = 50, offset: int = 0,
    ):
        return await TournamentRepository.get_all(
            session, status=status_filter, created_by=created_by,
            search=search, limit=limit, offset=offset,
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
        except Exception:
            pass

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

        # Initialize standings for each team
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

        batting_rows = await MatchRepository.get_batting_scorecards_by_tournament(session, tournament_id)
        bowling_rows = await MatchRepository.get_bowling_scorecards_by_tournament(session, tournament_id)

        # Aggregate batting stats per player
        batsmen = {}
        highest_scores = []
        for bat_card, player, innings in batting_rows:
            pid = player.id
            if pid not in batsmen:
                batsmen[pid] = {
                    "player_id": pid,
                    "player_name": player.full_name,
                    "matches": set(),
                    "total_runs": 0,
                    "total_balls": 0,
                    "total_fours": 0,
                    "total_sixes": 0,
                    "highest_score": 0,
                    "innings_played": 0,
                }
            batsmen[pid]["matches"].add(innings.match_id)
            batsmen[pid]["total_runs"] += bat_card.runs or 0
            batsmen[pid]["total_balls"] += bat_card.balls_faced or 0
            batsmen[pid]["total_fours"] += bat_card.fours or 0
            batsmen[pid]["total_sixes"] += bat_card.sixes or 0
            batsmen[pid]["innings_played"] += 1
            if (bat_card.runs or 0) > batsmen[pid]["highest_score"]:
                batsmen[pid]["highest_score"] = bat_card.runs or 0

            # Track individual high scores
            highest_scores.append({
                "player_id": pid,
                "player_name": player.full_name,
                "runs": bat_card.runs or 0,
                "balls_faced": bat_card.balls_faced or 0,
                "fours": bat_card.fours or 0,
                "sixes": bat_card.sixes or 0,
                "match_id": innings.match_id,
            })

        # Finalize batsmen stats
        batsmen_list = []
        for b in batsmen.values():
            avg = round(b["total_runs"] / b["innings_played"], 2) if b["innings_played"] > 0 else 0.0
            sr = round((b["total_runs"] / b["total_balls"]) * 100, 2) if b["total_balls"] > 0 else 0.0
            batsmen_list.append({
                "player_id": b["player_id"],
                "player_name": b["player_name"],
                "matches": len(b["matches"]),
                "innings": b["innings_played"],
                "runs": b["total_runs"],
                "balls_faced": b["total_balls"],
                "fours": b["total_fours"],
                "sixes": b["total_sixes"],
                "highest_score": b["highest_score"],
                "average": avg,
                "strike_rate": sr,
            })

        # Sort by runs desc (Orange Cap)
        batsmen_list.sort(key=lambda x: x["runs"], reverse=True)

        # Sort highest individual scores
        highest_scores.sort(key=lambda x: x["runs"], reverse=True)

        # Aggregate bowling stats per player
        bowlers = {}
        for bowl_card, player, innings in bowling_rows:
            pid = player.id
            if pid not in bowlers:
                bowlers[pid] = {
                    "player_id": pid,
                    "player_name": player.full_name,
                    "matches": set(),
                    "total_wickets": 0,
                    "total_runs_conceded": 0,
                    "total_overs": 0.0,
                    "total_maidens": 0,
                    "total_dot_balls": 0,
                    "innings_bowled": 0,
                    "best_wickets": 0,
                    "best_runs": 0,
                }
            bowlers[pid]["matches"].add(innings.match_id)
            bowlers[pid]["total_wickets"] += bowl_card.wickets or 0
            bowlers[pid]["total_runs_conceded"] += bowl_card.runs_conceded or 0
            bowlers[pid]["total_overs"] += bowl_card.overs_bowled or 0.0
            bowlers[pid]["total_maidens"] += bowl_card.maidens or 0
            bowlers[pid]["total_dot_balls"] += bowl_card.dot_balls or 0
            bowlers[pid]["innings_bowled"] += 1

            w = bowl_card.wickets or 0
            r = bowl_card.runs_conceded or 0
            if w > bowlers[pid]["best_wickets"] or (w == bowlers[pid]["best_wickets"] and r < bowlers[pid]["best_runs"]):
                bowlers[pid]["best_wickets"] = w
                bowlers[pid]["best_runs"] = r

        # Finalize bowlers stats
        bowlers_list = []
        for b in bowlers.values():
            economy = round(b["total_runs_conceded"] / b["total_overs"], 2) if b["total_overs"] > 0 else 0.0
            avg = round(b["total_runs_conceded"] / b["total_wickets"], 2) if b["total_wickets"] > 0 else 0.0
            bowlers_list.append({
                "player_id": b["player_id"],
                "player_name": b["player_name"],
                "matches": len(b["matches"]),
                "innings": b["innings_bowled"],
                "wickets": b["total_wickets"],
                "runs_conceded": b["total_runs_conceded"],
                "overs": b["total_overs"],
                "maidens": b["total_maidens"],
                "dot_balls": b["total_dot_balls"],
                "economy": economy,
                "average": avg,
                "best": f"{b['best_wickets']}/{b['best_runs']}",
            })

        # Sort by wickets desc (Purple Cap)
        bowlers_list.sort(key=lambda x: x["wickets"], reverse=True)

        # Aggregate fielding stats (catches, run outs, stumpings)
        from src.database.postgres.schemas.delivery_schema import DeliverySchema
        from src.database.postgres.schemas.innings_schema import InningsSchema
        from src.database.postgres.schemas.match_schema import MatchSchema
        from src.database.postgres.schemas.player_schema import PlayerSchema
        fielding_rows = await session.execute(
            select(
                DeliverySchema.fielder_id,
                PlayerSchema.full_name,
                DeliverySchema.wicket_type,
                func.count().label('cnt'),
            )
            .join(InningsSchema, DeliverySchema.innings_id == InningsSchema.id)
            .join(MatchSchema, InningsSchema.match_id == MatchSchema.id)
            .join(PlayerSchema, DeliverySchema.fielder_id == PlayerSchema.id)
            .where(
                MatchSchema.tournament_id == tournament_id,
                DeliverySchema.is_wicket == True,
                DeliverySchema.fielder_id.isnot(None),
            )
            .group_by(DeliverySchema.fielder_id, PlayerSchema.full_name, DeliverySchema.wicket_type)
        )
        fielders = {}
        for row in fielding_rows.all():
            pid = row.fielder_id
            if pid not in fielders:
                fielders[pid] = {"player_id": pid, "name": row.full_name, "catches": 0, "run_outs": 0, "stumpings": 0}
            wt = row.wicket_type
            if wt == 'caught':
                fielders[pid]["catches"] += row.cnt
            elif wt == 'run_out':
                fielders[pid]["run_outs"] += row.cnt
            elif wt == 'stumped':
                fielders[pid]["stumpings"] += row.cnt
        fielders_list = list(fielders.values())
        for f in fielders_list:
            f["total"] = f["catches"] + f["run_outs"] + f["stumpings"]
        fielders_list.sort(key=lambda x: x["total"], reverse=True)

        return {
            "top_batsmen": batsmen_list[:20],
            "top_bowlers": bowlers_list[:20],
            "top_fielders": fielders_list[:20],
            "highest_scores": highest_scores[:20],
        }
