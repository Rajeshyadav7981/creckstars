from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.innings_repository import InningsRepository
from src.database.postgres.repositories.scorecard_repository import ScorecardRepository
from src.database.postgres.repositories.delivery_repository import DeliveryRepository
from src.database.postgres.repositories.match_repository import MatchRepository
from src.database.postgres.schemas.player_schema import PlayerSchema
from src.database.postgres.schemas.team_schema import TeamSchema
from src.services.dls_service import calculate_dls_par_score
from sqlalchemy import select
from sqlalchemy.orm import load_only


class ScorecardService:

    @staticmethod
    async def get_full_scorecard(session: AsyncSession, match_id: int):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match:
            return None
        innings_list = await InningsRepository.get_by_match(session, match_id)

        # Pre-load team names — single batched query (was N session.get round-trips)
        team_name_map = {}
        _tids = [t for t in [match.team_a_id, match.team_b_id] if t]
        if _tids:
            _r = await session.execute(
                select(TeamSchema.id, TeamSchema.name).where(TeamSchema.id.in_(_tids))
            )
            team_name_map = {tid: tname for tid, tname in _r.all()}

        result = {
            "match_id": match_id,
            "status": match.status,
            "result": match.result_summary,
            "winner_id": match.winner_id,
            "overs": match.overs,
            "innings": [],
        }

        # Bulk-load batting/bowling/fow/partnerships for ALL innings in 4 queries
        # instead of 4 queries per innings (was 8 queries for a 2-innings match;
        # now 4 regardless of innings count). Group by innings_id below.
        from collections import defaultdict
        innings_ids = [inn.id for inn in innings_list]
        all_batting = await ScorecardRepository.get_batting_for_innings_ids(session, innings_ids)
        all_bowling = await ScorecardRepository.get_bowling_for_innings_ids(session, innings_ids)
        all_fow = await ScorecardRepository.get_fall_of_wickets_for_innings_ids(session, innings_ids)
        all_partnerships = await ScorecardRepository.get_partnerships_for_innings_ids(session, innings_ids)

        batting_by_inn = defaultdict(list)
        for b in all_batting:
            batting_by_inn[b.innings_id].append(b)
        bowling_by_inn = defaultdict(list)
        for b in all_bowling:
            bowling_by_inn[b.innings_id].append(b)
        fow_by_inn = defaultdict(list)
        for f in all_fow:
            fow_by_inn[f.innings_id].append(f)
        partnerships_by_inn = defaultdict(list)
        for p in all_partnerships:
            partnerships_by_inn[p.innings_id].append(p)

        # Bulk-load player names referenced anywhere in any innings — one query.
        all_player_ids = set()
        for b in all_batting:
            all_player_ids.add(b.player_id)
            if b.bowler_id:
                all_player_ids.add(b.bowler_id)
        for b in all_bowling:
            all_player_ids.add(b.player_id)
        for p in all_partnerships:
            if p.player_a_id: all_player_ids.add(p.player_a_id)
            if p.player_b_id: all_player_ids.add(p.player_b_id)
        all_player_names = {}
        if all_player_ids:
            pres = await session.execute(
                select(PlayerSchema.id, PlayerSchema.full_name)
                .where(PlayerSchema.id.in_(all_player_ids))
            )
            all_player_names = {pid: pname for pid, pname in pres.all()}

        for inn in innings_list:
            batting = batting_by_inn.get(inn.id, [])
            bowling = bowling_by_inn.get(inn.id, [])
            fow = fow_by_inn.get(inn.id, [])
            partnerships = partnerships_by_inn.get(inn.id, [])
            player_names = all_player_names

            batting_cards = []
            for b in batting:
                batting_cards.append({
                    "player_id": b.player_id,
                    "player_name": player_names.get(b.player_id, ""),
                    "batting_position": b.batting_position,
                    "runs": b.runs,
                    "balls_faced": b.balls_faced,
                    "fours": b.fours,
                    "sixes": b.sixes,
                    "strike_rate": b.strike_rate,
                    "how_out": b.how_out,
                    "is_out": b.is_out,
                })

            bowling_cards = []
            for b in bowling:
                bowling_cards.append({
                    "player_id": b.player_id,
                    "player_name": player_names.get(b.player_id, ""),
                    "overs_bowled": b.overs_bowled,
                    "maidens": b.maidens,
                    "runs_conceded": b.runs_conceded,
                    "wickets": b.wickets,
                    "economy_rate": b.economy_rate,
                    "wides": b.wides,
                    "no_balls": b.no_balls,
                    "dot_balls": b.dot_balls,
                })

            fow_list = []
            for f in fow:
                fow_list.append({
                    "wicket_number": f.wicket_number,
                    "player_id": f.player_id,
                    "player_name": player_names.get(f.player_id, ""),
                    "runs_at_fall": f.runs_at_fall,
                    "overs_at_fall": f.overs_at_fall,
                })

            partnership_list = []
            for p in partnerships:
                pa_name = player_names.get(p.player_a_id, "Unknown")
                pb_name = player_names.get(p.player_b_id, "Unknown")
                partnership_list.append({
                    "wicket_number": p.wicket_number,
                    "player_a_id": p.player_a_id,
                    "player_b_id": p.player_b_id,
                    "player_a_name": pa_name,
                    "player_b_name": pb_name,
                    "player_a_runs": getattr(p, 'player_a_runs', 0) or 0,
                    "player_b_runs": getattr(p, 'player_b_runs', 0) or 0,
                    "total_runs": p.total_runs,
                    "total_balls": p.total_balls,
                    "extras": getattr(p, 'extras', 0) or 0,
                    "is_active": p.is_active,
                })

            result["innings"].append({
                "innings_number": inn.innings_number,
                "batting_team_id": inn.batting_team_id,
                "bowling_team_id": inn.bowling_team_id,
                "batting_team_name": team_name_map.get(inn.batting_team_id, ""),
                "bowling_team_name": team_name_map.get(inn.bowling_team_id, ""),
                "is_super_over": inn.innings_number > 2,
                "total_runs": inn.total_runs,
                "total_wickets": inn.total_wickets,
                "total_overs": inn.total_overs,
                "total_extras": inn.total_extras,
                "batting": batting_cards,
                "bowling": bowling_cards,
                "fall_of_wickets": fow_list,
                "partnerships": partnership_list,
            })

        # Compute top performers for completed matches
        if match.status == "completed" and result["innings"]:
            result["top_performers"] = ScorecardService._compute_top_performers(result["innings"])

        return result

    @staticmethod
    def _compute_top_performers(innings_list: list) -> dict:
        """Derive Player of the Match, best batters, best bowlers from scorecard data.

        POM scoring follows ICC-style weighted impact system:
        - Batting: runs weighted by match context + milestone bonuses + SR bonus
        - Bowling: wickets heavily weighted + economy bonus + dot ball pressure
        - Match-winning contribution gets a bonus (winning team players)
        - Catches/run-outs (fielding) bonus via dismissal involvement
        """
        all_batters = []
        all_bowlers = []
        winning_team_id = None

        # Determine winning team (team that batted in the innings with higher total)
        if len(innings_list) >= 2:
            inn1 = innings_list[0]
            inn2 = innings_list[1]
            if inn2.get("total_runs", 0) > inn1.get("total_runs", 0):
                winning_team_id = inn2.get("batting_team_id")
            elif inn1.get("total_runs", 0) > inn2.get("total_runs", 0):
                winning_team_id = inn1.get("batting_team_id")

        for inn in innings_list:
            team = inn.get("batting_team_name", "")
            batting_team_id = inn.get("batting_team_id")
            bowling_team_id = inn.get("bowling_team_id")
            for b in inn.get("batting", []):
                all_batters.append({**b, "team_name": team, "team_id": batting_team_id,
                                    "innings_number": inn["innings_number"]})
            bowl_team = inn.get("bowling_team_name", "")
            for bw in inn.get("bowling", []):
                all_bowlers.append({**bw, "team_name": bowl_team, "team_id": bowling_team_id,
                                    "innings_number": inn["innings_number"]})

        player_scores = {}

        # ── Batting scoring ──
        for b in all_batters:
            pid = b["player_id"]
            runs = b.get("runs", 0)
            balls = b.get("balls_faced", 0)
            fours = b.get("fours", 0)
            sixes = b.get("sixes", 0)

            # Base: 1 point per run
            bat_score = runs

            # Boundary bonus: reward aggressive batting
            bat_score += fours * 1  # +1 per four (total 5 per four-run boundary)
            bat_score += sixes * 2  # +2 per six (total 8 per six-run hit)

            # Milestone bonuses (ICC-style)
            if runs >= 100:
                bat_score += 30  # Century bonus
            elif runs >= 50:
                bat_score += 15  # Half-century bonus
            elif runs >= 30:
                bat_score += 5   # Useful contribution bonus

            # Strike rate bonus (only if faced 10+ balls to avoid flukes)
            if balls >= 10:
                sr = (runs / balls) * 100
                if sr >= 200:
                    bat_score += 20
                elif sr >= 150:
                    bat_score += 12
                elif sr >= 130:
                    bat_score += 6

            # Not-out bonus (unbeaten innings are more valuable in chases)
            if not b.get("is_out", True) and runs >= 20:
                bat_score += 10

            # Winning team bonus (match-winning knock gets extra credit)
            if winning_team_id and b.get("team_id") == winning_team_id:
                bat_score = int(bat_score * 1.15)  # 15% bonus

            player_scores.setdefault(pid, {
                "player_id": pid, "player_name": b["player_name"],
                "score": 0, "batting": None, "bowling": None
            })
            player_scores[pid]["score"] += bat_score
            if not player_scores[pid]["batting"] or runs > player_scores[pid]["batting"].get("runs", 0):
                player_scores[pid]["batting"] = b

        # ── Bowling scoring ──
        for bw in all_bowlers:
            pid = bw["player_id"]
            wickets = bw.get("wickets", 0)
            overs = bw.get("overs_bowled", 0)
            economy = bw.get("economy_rate", 99)
            maidens = bw.get("maidens", 0)
            dots = bw.get("dot_balls", 0)
            runs_conceded = bw.get("runs_conceded", 0)

            # Base: 25 per wicket (wickets are the most valuable bowling contribution)
            bowl_score = wickets * 25

            # Multi-wicket haul bonuses (ICC-style)
            if wickets >= 5:
                bowl_score += 30  # 5-wicket haul bonus
            elif wickets >= 4:
                bowl_score += 15
            elif wickets >= 3:
                bowl_score += 8

            # Economy bonus (only if bowled 2+ overs to be meaningful)
            if overs >= 2:
                if economy < 4:
                    bowl_score += 20
                elif economy < 6:
                    bowl_score += 12
                elif economy < 7:
                    bowl_score += 6

            # Dot ball pressure (building pressure is crucial)
            bowl_score += dots * 1

            # Maiden over bonus
            bowl_score += maidens * 8

            # Winning team bonus
            if winning_team_id and bw.get("team_id") == winning_team_id:
                bowl_score = int(bowl_score * 1.15)

            player_scores.setdefault(pid, {
                "player_id": pid, "player_name": bw["player_name"],
                "score": 0, "batting": None, "bowling": None
            })
            player_scores[pid]["score"] += bowl_score
            if not player_scores[pid]["bowling"] or wickets > (player_scores[pid]["bowling"] or {}).get("wickets", 0):
                player_scores[pid]["bowling"] = bw

        # ── Fielding bonus (from dismissal records) ──
        for inn in innings_list:
            for b in inn.get("batting", []):
                if b.get("is_out") and b.get("fielder_id"):
                    fid = b["fielder_id"]
                    if fid in player_scores:
                        player_scores[fid]["score"] += 10  # Catch/stumping/run-out credit

        # Player of the Match = highest combined score
        pom = max(player_scores.values(), key=lambda x: x["score"]) if player_scores else None

        # Best batters: top 3 by runs, then strike rate
        best_batters = sorted(all_batters, key=lambda x: (-x.get("runs", 0), -(x.get("strike_rate") or 0)))[:3]

        # Best bowlers: top 3 by wickets, then economy (lower is better)
        best_bowlers = sorted(all_bowlers, key=lambda x: (-x.get("wickets", 0), x.get("economy_rate", 99)))[:3]

        # Per-innings top performers
        innings_top = []
        for inn in innings_list:
            top_bat = max(inn.get("batting", [{}]), key=lambda x: x.get("runs", 0), default=None)
            top_bowl = max(inn.get("bowling", [{}]), key=lambda x: x.get("wickets", 0), default=None)
            innings_top.append({
                "innings_number": inn["innings_number"],
                "batting_team_name": inn.get("batting_team_name", ""),
                "top_batter": top_bat,
                "top_bowler": top_bowl,
            })

        pom_data = None
        if pom:
            pom_data = {
                "player_id": pom["player_id"],
                "player_name": pom["player_name"],
                "batting": pom["batting"],
                "bowling": pom["bowling"],
            }

        return {
            "player_of_match": pom_data,
            "best_batters": best_batters,
            "best_bowlers": best_bowlers,
            "innings_top": innings_top,
        }

    @staticmethod
    async def get_live_state(session: AsyncSession, match_id: int):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match:
            return None

        if match.status == "completed":
            # Pull the latest innings so the frontend can render a rich
            # "match over" card (team / score / overs / target) instead of a
            # bare stub. Keeps the screen visually consistent with the
            # innings-break dialog.
            all_innings = await InningsRepository.get_by_match(session, match_id)
            last_inn = all_innings[-1] if all_innings else None
            batting_team_name = None
            bowling_team_name = None
            target = None
            if last_inn:
                # Batched team-name lookup (1 query instead of 2 session.get round-trips)
                _ids = [t for t in [last_inn.batting_team_id, last_inn.bowling_team_id] if t]
                _names = {}
                if _ids:
                    _r = await session.execute(
                        select(TeamSchema.id, TeamSchema.name).where(TeamSchema.id.in_(_ids))
                    )
                    _names = {tid: tname for tid, tname in _r.all()}
                batting_team_name = _names.get(last_inn.batting_team_id)
                bowling_team_name = _names.get(last_inn.bowling_team_id)
                target = last_inn.target
            return {
                "match_id": match_id,
                "status": "completed",
                "message": "Match completed",
                "result_summary": match.result_summary,
                "winner_id": match.winner_id,
                # Last innings data for rich UI rendering
                "innings_number": last_inn.innings_number if last_inn else None,
                "batting_team_id": last_inn.batting_team_id if last_inn else None,
                "bowling_team_id": last_inn.bowling_team_id if last_inn else None,
                "batting_team_name": batting_team_name,
                "bowling_team_name": bowling_team_name,
                "total_runs": last_inn.total_runs if last_inn else 0,
                "total_wickets": last_inn.total_wickets if last_inn else 0,
                "total_overs": last_inn.total_overs if last_inn else 0,
                "target": target,
                "is_super_over": (last_inn.innings_number > 2) if last_inn else False,
            }

        if not match.current_innings:
            return {"match_id": match_id, "status": match.status, "message": "Match not started"}

        innings_list = await InningsRepository.get_by_match(session, match_id, match.current_innings)
        if not innings_list:
            return {"match_id": match_id, "status": match.status}
        innings = innings_list[0]

        # Detect innings break: innings completed but match still live (waiting for next innings)
        if innings.status == "completed" and match.status == "live":
            # Batched team-name lookup (1 query instead of 2 session.get round-trips)
            _ids = [t for t in [innings.batting_team_id, innings.bowling_team_id] if t]
            _names = {}
            if _ids:
                _r = await session.execute(
                    select(TeamSchema.id, TeamSchema.name).where(TeamSchema.id.in_(_ids))
                )
                _names = {tid: tname for tid, tname in _r.all()}
            bat_name = _names.get(innings.batting_team_id) or "Team"
            bowl_name = _names.get(innings.bowling_team_id) or "Team"
            is_super_over = innings.innings_number > 2

            # Detect tie: for even innings (2nd of pair), compare with previous innings
            is_tied = False
            if innings.innings_number >= 2 and innings.innings_number % 2 == 0:
                prev_inn = await InningsRepository.get_by_match(session, match_id, innings.innings_number - 1)
                if prev_inn and prev_inn[0].total_runs == innings.total_runs:
                    is_tied = True

            return {
                "match_id": match_id,
                "status": "live",
                "innings_break": True,
                "is_super_over": is_super_over,
                "is_tied": is_tied,
                "innings_number": innings.innings_number,
                "batting_team_name": bat_name,
                "bowling_team_name": bowl_name,
                "total_runs": innings.total_runs,
                "total_wickets": innings.total_wickets,
                "total_overs": innings.total_overs,
                "target": (innings.total_runs or 0) + 1,
                "message": f"{'Super Over' if is_super_over else 'Innings'} {innings.innings_number} completed. {bat_name} scored {innings.total_runs}/{innings.total_wickets}",
            }

        # Get team names — single batched query instead of two session.get() round-trips
        team_ids_for_innings = [t for t in [innings.batting_team_id, innings.bowling_team_id] if t]
        team_name_lookup = {}
        if team_ids_for_innings:
            t_res = await session.execute(
                select(TeamSchema.id, TeamSchema.name).where(TeamSchema.id.in_(team_ids_for_innings))
            )
            team_name_lookup = {tid: tname for tid, tname in t_res.all()}
        batting_team_name = team_name_lookup.get(innings.batting_team_id) or "Team A"
        bowling_team_name = team_name_lookup.get(innings.bowling_team_id) or "Team B"

        # Get player names
        player_ids = [innings.current_striker_id, innings.current_non_striker_id, innings.current_bowler_id]
        player_names = {}
        if any(player_ids):
            res = await session.execute(select(PlayerSchema).options(load_only(PlayerSchema.id, PlayerSchema.full_name)).where(PlayerSchema.id.in_([p for p in player_ids if p])))
            for p in res.scalars().all():
                player_names[p.id] = p.full_name

        # Striker batting card
        striker_info = None
        if innings.current_striker_id:
            card = await ScorecardRepository.get_or_create_batting(session, innings.id, innings.current_striker_id)
            striker_info = {
                "player_id": card.player_id,
                "name": player_names.get(card.player_id, ""),
                "runs": card.runs,
                "balls": card.balls_faced,
                "fours": card.fours,
                "sixes": card.sixes,
                "strike_rate": card.strike_rate,
            }

        non_striker_info = None
        if innings.current_non_striker_id:
            card = await ScorecardRepository.get_or_create_batting(session, innings.id, innings.current_non_striker_id)
            non_striker_info = {
                "player_id": card.player_id,
                "name": player_names.get(card.player_id, ""),
                "runs": card.runs,
                "balls": card.balls_faced,
                "fours": card.fours,
                "sixes": card.sixes,
                "strike_rate": card.strike_rate,
            }

        bowler_info = None
        if innings.current_bowler_id:
            bcard = await ScorecardRepository.get_or_create_bowling(session, innings.id, innings.current_bowler_id)
            bowler_info = {
                "player_id": bcard.player_id,
                "name": player_names.get(bcard.player_id, ""),
                "overs": bcard.overs_bowled,
                "maidens": bcard.maidens,
                "runs": bcard.runs_conceded,
                "wickets": bcard.wickets,
                "economy": bcard.economy_rate,
            }

        # Fetch dismissed player IDs — single narrow query instead of loading
        # all batting cards. This runs on every live_state poll (~1/s).
        from src.database.postgres.schemas.batting_scorecard_schema import BattingScorecardSchema as _BS
        _dres = await session.execute(
            select(_BS.player_id).where(_BS.innings_id == innings.id, _BS.is_out == True)
        )
        dismissed_player_ids = [r[0] for r in _dres.all()]

        # This over balls
        deliveries = await DeliveryRepository.get_by_innings(session, innings.id, innings.current_over)
        this_over = []
        for d in deliveries:
            ball_display = str(d.total_runs)
            if d.is_wicket:
                ball_display = "W"
            elif d.extra_type == "wide":
                ball_display = f"{d.total_runs}wd"
            elif d.extra_type == "noball":
                ball_display = f"{d.total_runs}nb"
            elif d.extra_type in ("bye", "legbye"):
                ball_display = f"{d.total_runs}b"
            this_over.append(ball_display)

        # Run rate — super over innings (innings_number > 2) are 1 over max
        total_balls = innings.current_over * 6 + innings.current_ball
        innings_max_overs = 1 if innings.innings_number > 2 else match.overs
        run_rate = (innings.total_runs / (total_balls / 6)) if total_balls > 0 else 0.0
        required_rate = None
        if innings.target:
            remaining_runs = innings.target - innings.total_runs
            remaining_balls = (innings_max_overs * 6) - total_balls
            required_rate = (remaining_runs / (remaining_balls / 6)) if remaining_balls > 0 else 0.0

        # Remaining runs/balls for chase info
        remaining_runs = None
        remaining_balls = None
        if innings.target:
            remaining_runs = max(0, innings.target - innings.total_runs)
            remaining_balls = max(0, (innings_max_overs * 6) - total_balls)

        # DLS par score (2nd innings only, not super overs)
        dls_par = None
        if innings.target and innings.innings_number == 2 and total_balls > 0:
            first_innings_total = innings.target - 1  # target = first innings + 1
            overs_bowled = total_balls / 6.0
            dls_data = calculate_dls_par_score(
                first_innings_total=first_innings_total,
                total_overs=match.overs,
                overs_bowled=overs_bowled,
                wickets_lost=innings.total_wickets,
            )
            if dls_data:
                dls_par = dls_data["par_score"]

        return {
            "match_id": match_id,
            "status": match.status,
            "innings_number": innings.innings_number,
            "batting_team_id": innings.batting_team_id,
            "bowling_team_id": innings.bowling_team_id,
            "batting_team_name": batting_team_name,
            "bowling_team_name": bowling_team_name,
            "total_runs": innings.total_runs,
            "total_wickets": innings.total_wickets,
            "total_overs": innings.total_overs,
            "current_over": innings.current_over,
            "current_ball": innings.current_ball,
            "target": innings.target,
            "remaining_runs": remaining_runs,
            "remaining_balls": remaining_balls,
            "run_rate": round(run_rate, 2),
            "required_rate": round(required_rate, 2) if required_rate is not None else None,
            "striker": striker_info,
            "non_striker": non_striker_info,
            "bowler": bowler_info,
            "this_over": this_over,
            "dismissed_player_ids": dismissed_player_ids,
            "is_free_hit": innings.is_free_hit or False,
            "dls_par": dls_par,
        }

    @staticmethod
    async def get_commentary(session: AsyncSession, match_id: int, innings_number: int = None, limit: int = 20, offset: int = 0):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match:
            return []
        inn_num = innings_number or match.current_innings or 1
        innings_list = await InningsRepository.get_by_match(session, match_id, inn_num)
        if not innings_list:
            return []
        innings = innings_list[0]

        deliveries = await DeliveryRepository.get_commentary(session, innings.id, limit=limit, offset=offset)

        # Gather all player IDs from deliveries
        player_ids = set()
        for d in deliveries:
            player_ids.add(d.striker_id)
            player_ids.add(d.bowler_id)
            if d.dismissed_player_id:
                player_ids.add(d.dismissed_player_id)
            if d.fielder_id:
                player_ids.add(d.fielder_id)

        player_names = {}
        if player_ids:
            res = await session.execute(select(PlayerSchema).where(PlayerSchema.id.in_(player_ids)))
            for p in res.scalars().all():
                player_names[p.id] = p.full_name

        result = []
        for d in deliveries:
            result.append({
                "over": d.over_number,
                "ball": d.ball_number,
                "striker_id": d.striker_id,
                "striker_name": player_names.get(d.striker_id, ""),
                "bowler_id": d.bowler_id,
                "bowler_name": player_names.get(d.bowler_id, ""),
                "batsman_runs": d.batsman_runs,
                "extra_type": d.extra_type,
                "extra_runs": d.extra_runs,
                "total_runs": d.total_runs,
                "is_wicket": d.is_wicket,
                "wicket_type": d.wicket_type,
                "dismissed_player_id": d.dismissed_player_id,
                "dismissed_player_name": player_names.get(d.dismissed_player_id, "") if d.dismissed_player_id else None,
                "fielder_id": d.fielder_id,
                "fielder_name": player_names.get(d.fielder_id, "") if d.fielder_id else None,
                "is_boundary": d.is_boundary,
                "is_six": d.is_six,
                "is_legal": d.is_legal,
                "commentary": d.commentary,
            })
        return result
