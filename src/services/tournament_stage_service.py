from fastapi import HTTPException, status
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from itertools import combinations

from src.database.postgres.repositories.tournament_stage_repository import TournamentStageRepository
from src.database.postgres.repositories.match_repository import MatchRepository
from src.database.postgres.repositories.tournament_repository import TournamentRepository
from src.database.postgres.schemas.match_schema import MatchSchema
from src.database.postgres.schemas.innings_schema import InningsSchema
from src.database.postgres.schemas.tournament_stage_schema import TournamentStageSchema
from src.database.postgres.schemas.tournament_group_schema import TournamentGroupSchema
from src.database.postgres.schemas.tournament_group_team_schema import TournamentGroupTeamSchema
from src.database.postgres.schemas.team_schema import TeamSchema
from src.utils.logger import get_logger

logger = get_logger(__name__)


class TournamentStageService:

    @staticmethod
    async def setup_stages(session, tournament_id, stages_config):
        """Create stages for a tournament.
        stages_config: [{"name": "group_stage", "qualification_rule": {"top_n": 2, "from": "each_group"}}, ...]
        """
        tournament = await TournamentRepository.get_by_id(session, tournament_id)
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")

        # Get max existing stage_order to avoid unique constraint violation
        existing = await TournamentStageRepository.get_stages(session, tournament_id)
        max_order = max((s.stage_order for s in existing), default=0) if existing else 0

        created = []
        for i, cfg in enumerate(stages_config):
            stage = await TournamentStageRepository.create_stage(session, {
                "tournament_id": tournament_id,
                "stage_name": cfg["name"],
                "stage_order": max_order + i + 1,
                "status": "upcoming",
                "qualification_rule": cfg.get("qualification_rule"),
            })
            created.append(stage)
        await session.commit()
        return created

    @staticmethod
    async def setup_groups(session, tournament_id, stage_id, groups_config):
        """Create groups within a stage and assign teams.
        groups_config: [{"name": "Group A", "team_ids": [1, 2, 3, 4]}, ...]
        """
        stage = await TournamentStageRepository.get_stage_by_id(session, stage_id)
        if not stage or stage.tournament_id != tournament_id:
            raise HTTPException(status_code=404, detail="Stage not found")

        created = []
        for i, cfg in enumerate(groups_config):
            group = await TournamentStageRepository.create_group(session, {
                "stage_id": stage_id,
                "group_name": cfg["name"],
                "group_order": i,
            })
            for tid in cfg.get("team_ids", []):
                await TournamentStageRepository.add_team_to_group(session, group.id, tid)
            created.append(group)

        # Mark first stage as in_progress if it's the first
        stages = await TournamentStageRepository.get_stages(session, tournament_id)
        if stages and stages[0].id == stage_id:
            await TournamentStageRepository.update_stage(session, stage_id, {"status": "in_progress"})

        await session.commit()
        return created

    @staticmethod
    async def generate_group_matches(session, tournament_id, stage_id):
        """Generate matches for a stage. Round-robin for group stages, knockout pairs for knockout stages."""
        stage = await TournamentStageRepository.get_stage_by_id(session, stage_id)
        if not stage:
            raise HTTPException(status_code=404, detail="Stage not found")

        tournament = await TournamentRepository.get_by_id(session, tournament_id)
        is_knockout = stage.stage_name in ("quarter_final", "semi_final", "final")

        # Block knockout match generation if previous stage isn't completed
        if is_knockout:
            all_stages = await TournamentStageRepository.get_stages(session, tournament_id)
            prev_stages = [s for s in all_stages if s.stage_order < stage.stage_order]
            if prev_stages:
                last_prev = prev_stages[-1]
                if last_prev.status != "completed":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot generate {stage.stage_name.replace('_', ' ')} matches — "
                               f"{last_prev.stage_name.replace('_', ' ')} is not completed yet"
                    )

        groups = await TournamentStageRepository.get_groups(session, stage_id)

        # If knockout and no groups exist yet, create a group and populate with qualified teams
        if is_knockout and not groups:
            # Get qualified teams from previous completed stages
            all_stages = await TournamentStageRepository.get_stages(session, tournament_id)
            prev_stages = [s for s in all_stages if s.stage_order < stage.stage_order and s.status == "completed"]

            qualified_team_ids = []
            if prev_stages:
                last_prev = prev_stages[-1]
                prev_groups = await TournamentStageRepository.get_groups(session, last_prev.id)
                for pg in prev_groups:
                    team_rows = await TournamentStageRepository.get_group_teams(session, pg.id)
                    for team, gt in team_rows:
                        if gt.qualification_status == "qualified" and team.id not in qualified_team_ids:
                            qualified_team_ids.append(team.id)

            # If no previous stages or no qualified teams, use all tournament teams
            # (e.g., direct Final without group stages)
            if len(qualified_team_ids) < 2:
                from src.database.postgres.schemas.tournament_team_schema import TournamentTeamSchema
                tt_result = await session.execute(
                    select(TournamentTeamSchema.team_id).where(
                        TournamentTeamSchema.tournament_id == tournament_id
                    )
                )
                qualified_team_ids = [r[0] for r in tt_result.all()]

            if len(qualified_team_ids) < 2:
                raise HTTPException(status_code=400, detail="Not enough teams for knockout (need at least 2)")

            # Validate team count per stage type
            stage_team_limits = {"quarter_final": 8, "semi_final": 4, "final": 2}
            limit = stage_team_limits.get(stage.stage_name)
            if limit and len(qualified_team_ids) > limit:
                qualified_team_ids = qualified_team_ids[:limit]
            if limit and len(qualified_team_ids) < limit:
                # For SF with < 4 teams but >= 2: just do Final
                if stage.stage_name == "semi_final" and len(qualified_team_ids) >= 2:
                    qualified_team_ids = qualified_team_ids[:2]
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{stage.stage_name.replace('_', ' ').title()} needs {limit} teams, but only {len(qualified_team_ids)} qualified"
                    )

            # Create a single group for the knockout stage
            group = await TournamentStageRepository.create_group(session, {
                "stage_id": stage_id,
                "group_name": stage.stage_name.replace("_", " ").title(),
                "group_order": 0,
            })
            for tid in qualified_team_ids:
                await TournamentStageRepository.add_team_to_group(session, group.id, tid)
            groups = [group]

        # Count existing matches for numbering (count only, no row data needed)
        count_result = await session.execute(
            select(sa_func.count(MatchSchema.id)).where(MatchSchema.tournament_id == tournament_id)
        )
        match_num = (count_result.scalar() or 0) + 1

        all_matches = []
        for group in groups:
            team_rows = await TournamentStageRepository.get_group_teams(session, group.id)
            team_ids = [team.id for team, gt in team_rows]

            if is_knockout:
                # Knockout: pair 1st vs last, 2nd vs second-last (cross-seeding)
                pairs = []
                for i in range(len(team_ids) // 2):
                    pairs.append((team_ids[i], team_ids[len(team_ids) - 1 - i]))
                for ta_id, tb_id in pairs:
                    match = await MatchRepository.create(session, {
                        "tournament_id": tournament_id,
                        "team_a_id": ta_id,
                        "team_b_id": tb_id,
                        "overs": tournament.overs_per_match,
                        "match_type": stage.stage_name,
                        "stage_id": stage_id,
                        "group_id": group.id,
                        "match_number": match_num,
                        "created_by": tournament.created_by,
                    })
                    all_matches.append(match)
                    match_num += 1
            else:
                # Round-robin: every pair plays once
                for ta_id, tb_id in combinations(team_ids, 2):
                    match = await MatchRepository.create(session, {
                        "tournament_id": tournament_id,
                        "team_a_id": ta_id,
                        "team_b_id": tb_id,
                        "overs": tournament.overs_per_match,
                        "match_type": stage.stage_name,
                        "stage_id": stage_id,
                        "group_id": group.id,
                        "match_number": match_num,
                        "created_by": tournament.created_by,
                    })
                    all_matches.append(match)
                    match_num += 1

        # Mark stage as in_progress if matches were created
        if all_matches and stage.status == "upcoming":
            await TournamentStageRepository.update_stage(session, stage_id, {"status": "in_progress"})

        await session.commit()
        return all_matches

    @staticmethod
    async def get_group_standings(session, group_id):
        """Compute standings for a specific group (same logic as TournamentService.get_standings but scoped to group).
        Also returns total_matches and completed_matches counts.
        """
        # Get teams in group
        team_rows = await TournamentStageRepository.get_group_teams(session, group_id)
        if not team_rows:
            return {"standings": [], "total_matches": 0, "completed_matches": 0}

        team_map = {}
        standings = {}
        for team, gt in team_rows:
            team_map[team.id] = team
            standings[team.id] = {
                "team_id": team.id,
                "team_name": team.name,
                "short_name": team.short_name,
                "played": 0, "won": 0, "lost": 0, "drawn": 0, "points": 0,
                "runs_scored": 0, "overs_faced": 0.0,
                "runs_conceded": 0, "overs_bowled": 0.0,
                "nrr": 0.0,
                "qualification_status": gt.qualification_status,
            }

        # Get all matches in this group (for counts + standings)
        all_result = await session.execute(
            select(MatchSchema).options(load_only(
                MatchSchema.id, MatchSchema.status, MatchSchema.team_a_id,
                MatchSchema.team_b_id, MatchSchema.winner_id, MatchSchema.result_type,
                MatchSchema.tournament_id, MatchSchema.group_id,
            )).where(MatchSchema.group_id == group_id)
        )
        all_group_matches = all_result.scalars().all()
        total_matches = len(all_group_matches)
        completed_matches = sum(1 for m in all_group_matches if m.status == "completed")

        # Get completed matches in this group
        matches = [m for m in all_group_matches if m.status == "completed"]

        # Get innings for those matches
        match_ids = [m.id for m in matches]
        innings_by_match = {}
        if match_ids:
            result = await session.execute(
                select(InningsSchema).where(InningsSchema.match_id.in_(match_ids))
            )
            for inn in result.scalars().all():
                innings_by_match.setdefault(inn.match_id, []).append(inn)

        # Get tournament points config
        pts_win, pts_draw, pts_nr = 2, 1, 0
        if matches:
            try:
                match0 = matches[0]
                if match0.tournament_id:
                    tourn = await TournamentRepository.get_by_id(session, match0.tournament_id)
                    if tourn:
                        pts_win = tourn.points_per_win if hasattr(tourn, 'points_per_win') and tourn.points_per_win else 2
                        pts_draw = tourn.points_per_draw if hasattr(tourn, 'points_per_draw') and tourn.points_per_draw else 1
                        pts_nr = tourn.points_per_no_result if hasattr(tourn, 'points_per_no_result') and tourn.points_per_no_result else 0
            except Exception as e:
                logger.warning(f"Failed to load tournament points config: {e}")

        # Process matches
        for match in matches:
            ta, tb = match.team_a_id, match.team_b_id
            if ta not in standings or tb not in standings:
                continue

            rt = getattr(match, 'result_type', None) or 'normal'

            if rt in ('no_result', 'abandoned'):
                standings[ta]["played"] += 1
                standings[tb]["played"] += 1
                standings[ta]["no_result"] = standings[ta].get("no_result", 0) + 1
                standings[tb]["no_result"] = standings[tb].get("no_result", 0) + 1
                standings[ta]["points"] += pts_nr
                standings[tb]["points"] += pts_nr
                continue

            standings[ta]["played"] += 1
            standings[tb]["played"] += 1
            if match.winner_id:
                if match.winner_id == ta:
                    standings[ta]["won"] += 1
                    standings[ta]["points"] += pts_win
                    standings[tb]["lost"] += 1
                elif match.winner_id == tb:
                    standings[tb]["won"] += 1
                    standings[tb]["points"] += pts_win
                    standings[ta]["lost"] += 1
            else:
                standings[ta]["drawn"] += 1
                standings[tb]["drawn"] += 1
                standings[ta]["points"] += pts_draw
                standings[tb]["points"] += pts_draw

            if rt in ('walkover', 'forfeit', 'awarded'):
                continue

            for inn in innings_by_match.get(match.id, []):
                bat, bowl = inn.batting_team_id, inn.bowling_team_id
                if bat in standings:
                    standings[bat]["runs_scored"] += inn.total_runs or 0
                    standings[bat]["overs_faced"] += inn.total_overs or 0.0
                if bowl in standings:
                    standings[bowl]["runs_conceded"] += inn.total_runs or 0
                    standings[bowl]["overs_bowled"] += inn.total_overs or 0.0

        # Calculate NRR
        for s in standings.values():
            rr_for = (s["runs_scored"] / s["overs_faced"]) if s["overs_faced"] > 0 else 0
            rr_against = (s["runs_conceded"] / s["overs_bowled"]) if s["overs_bowled"] > 0 else 0
            s["nrr"] = round(rr_for - rr_against, 3)

        sorted_standings = sorted(
            standings.values(),
            key=lambda x: (x["points"], x["nrr"]),
            reverse=True,
        )

        # Remove intermediate fields
        for s in sorted_standings:
            for k in ("runs_scored", "overs_faced", "runs_conceded", "overs_bowled"):
                del s[k]

        return {
            "standings": sorted_standings,
            "total_matches": total_matches,
            "completed_matches": completed_matches,
        }

    @staticmethod
    async def get_stage_standings(session, stage_id):
        """Get standings for all groups in a stage."""
        groups = await TournamentStageRepository.get_groups(session, stage_id)
        result = []
        for group in groups:
            group_data = await TournamentStageService.get_group_standings(session, group.id)
            result.append({
                "group_id": group.id,
                "group_name": group.group_name,
                "standings": group_data["standings"],
                "total_matches": group_data["total_matches"],
                "completed_matches": group_data["completed_matches"],
            })
        return result

    @staticmethod
    async def get_stages_with_details(session, tournament_id):
        """Get all stages with their groups, teams, and match counts."""
        stages = await TournamentStageRepository.get_stages(session, tournament_id)
        result = []
        for stage in stages:
            groups = await TournamentStageRepository.get_groups(session, stage.id)
            groups_data = []
            for g in groups:
                team_rows = await TournamentStageRepository.get_group_teams(session, g.id)
                teams = [
                    {
                        "team_id": t.id,
                        "team_name": t.name,
                        "short_name": t.short_name,
                        "qualification_status": gt.qualification_status,
                    }
                    for t, gt in team_rows
                ]

                # Count matches (only need id + status)
                match_result = await session.execute(
                    select(MatchSchema).options(load_only(
                        MatchSchema.id, MatchSchema.status,
                    )).where(MatchSchema.group_id == g.id)
                )
                group_matches = match_result.scalars().all()
                total = len(group_matches)
                completed = sum(1 for m in group_matches if m.status == "completed")

                groups_data.append({
                    "group_id": g.id,
                    "group_name": g.group_name,
                    "teams": teams,
                    "total_matches": total,
                    "completed_matches": completed,
                })

            # Also get matches directly assigned to this stage (not via group)
            stage_match_result = await session.execute(
                select(MatchSchema).options(load_only(
                    MatchSchema.id, MatchSchema.team_a_id, MatchSchema.team_b_id,
                    MatchSchema.status, MatchSchema.result_summary, MatchSchema.match_date,
                    MatchSchema.time_slot, MatchSchema.match_type, MatchSchema.stage_id,
                    MatchSchema.tournament_id,
                )).where(
                    MatchSchema.stage_id == stage.id,
                    MatchSchema.tournament_id == tournament_id,
                )
            )
            stage_matches = stage_match_result.scalars().all()

            # Batch-load all teams for this stage's matches (1 query instead of 2 per match)
            stage_team_ids = set()
            for m in stage_matches:
                if m.team_a_id: stage_team_ids.add(m.team_a_id)
                if m.team_b_id: stage_team_ids.add(m.team_b_id)
            stage_teams = {}
            if stage_team_ids:
                t_res = await session.execute(
                    select(TeamSchema).options(load_only(TeamSchema.id, TeamSchema.name, TeamSchema.short_name))
                    .where(TeamSchema.id.in_(stage_team_ids))
                )
                for t in t_res.scalars().all():
                    stage_teams[t.id] = t

            matches_data = []
            for m in stage_matches:
                ta = stage_teams.get(m.team_a_id)
                tb = stage_teams.get(m.team_b_id)
                matches_data.append({
                    "id": m.id,
                    "team_a_id": m.team_a_id,
                    "team_b_id": m.team_b_id,
                    "team_a_name": ta.name if ta else None,
                    "team_b_name": tb.name if tb else None,
                    "team_a_short": ta.short_name if ta else None,
                    "team_b_short": tb.short_name if tb else None,
                    "status": m.status,
                    "result_summary": m.result_summary,
                    "match_date": m.match_date.isoformat() if m.match_date else None,
                    "time_slot": m.time_slot,
                    "match_type": m.match_type,
                })

            result.append({
                "stage_id": stage.id,
                "stage_name": stage.stage_name,
                "stage_order": stage.stage_order,
                "status": stage.status,
                "qualification_rule": stage.qualification_rule,
                "groups": groups_data,
                "matches": matches_data,
                "total_matches": len(stage_matches),
                "completed_matches": sum(1 for m in stage_matches if m.status == "completed"),
            })
        return result

    @staticmethod
    async def move_team_between_groups(session, tournament_id, stage_id, team_id, from_group_id, to_group_id):
        """Move a team from one group to another within the same stage."""
        stage = await TournamentStageRepository.get_stage_by_id(session, stage_id)
        if not stage or stage.tournament_id != tournament_id:
            raise HTTPException(status_code=404, detail="Stage not found")

        # Check no completed matches involve this team in the source group (existence only)
        result = await session.execute(
            select(MatchSchema.id).where(
                MatchSchema.group_id == from_group_id,
                MatchSchema.status == "completed",
                ((MatchSchema.team_a_id == team_id) | (MatchSchema.team_b_id == team_id)),
            ).limit(1)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Cannot move team with completed matches in this group")

        # Remove from source group
        removed = await TournamentStageRepository.remove_team_from_group(session, from_group_id, team_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Team not found in source group")

        # Delete any upcoming matches for this team in the source group
        upcoming = await session.execute(
            select(MatchSchema).options(load_only(
                MatchSchema.id,
            )).where(
                MatchSchema.group_id == from_group_id,
                MatchSchema.status == "upcoming",
                ((MatchSchema.team_a_id == team_id) | (MatchSchema.team_b_id == team_id)),
            )
        )
        for m in upcoming.scalars().all():
            await session.delete(m)

        # Add to target group
        await TournamentStageRepository.add_team_to_group(session, to_group_id, team_id)

        await session.commit()
        return {"message": "Team moved successfully"}

    @staticmethod
    async def on_match_completed(session, match_id):
        """Called after a match is completed. Checks stage progression and auto-updates statuses."""
        match = await MatchRepository.get_by_id(session, match_id)
        if not match or not match.stage_id:
            return

        stage = await TournamentStageRepository.get_stage_by_id(session, match.stage_id)
        if not stage:
            return

        tournament_id = match.tournament_id or stage.tournament_id

        # --- Auto-update tournament status to "in_progress" if it's still "upcoming" ---
        tournament = await TournamentRepository.get_by_id(session, tournament_id)
        if tournament and tournament.status == "upcoming":
            await TournamentRepository.update(session, tournament_id, {"status": "in_progress"})

        # --- Auto-update stage status to "in_progress" if it's still "upcoming" ---
        if stage.status == "upcoming":
            await TournamentStageRepository.update_stage(session, stage.id, {"status": "in_progress"})

        # Check if all matches in this stage are completed
        result = await session.execute(
            select(MatchSchema).options(load_only(
                MatchSchema.id, MatchSchema.status, MatchSchema.match_number,
                MatchSchema.winner_id, MatchSchema.team_a_id, MatchSchema.team_b_id,
            )).where(MatchSchema.stage_id == stage.id)
        )
        stage_matches = result.scalars().all()
        all_completed = all(m.status == "completed" for m in stage_matches)

        if not all_completed:
            # Stage not yet complete, but we already updated tournament/stage status above
            await session.commit()
            return

        # Stage is complete - update stage status
        await TournamentStageRepository.update_stage(session, stage.id, {"status": "completed"})

        # Auto-complete tournament ONLY when a "final" stage completes
        if stage.stage_name == "final":
            await TournamentRepository.update(session, tournament_id, {"status": "completed"})

        # Get qualification rule
        rule = stage.qualification_rule or {}
        top_n = rule.get("top_n", 2)

        # Process qualifications — different logic for group vs knockout stages
        groups = await TournamentStageRepository.get_groups(session, stage.id)
        qualified_teams = []
        is_knockout_stage = stage.stage_name in ("quarter_final", "semi_final", "final")

        if is_knockout_stage:
            # Knockout: winners advance, in match order (important for bracket seeding)
            # Match 1 winner = seed 1, Match 2 winner = seed 2, etc.
            ordered_matches = sorted(stage_matches, key=lambda m: m.match_number or m.id)
            match_idx = 0
            for m in ordered_matches:
                if m.winner_id:
                    match_idx += 1
                    loser_id = m.team_b_id if m.winner_id == m.team_a_id else m.team_a_id
                    qualified_teams.append({
                        "team_id": m.winner_id,
                        "group_rank": match_idx,  # seed by match order
                        "group_name": f"Match {match_idx}",
                        "match_number": m.match_number or m.id,
                    })
                    # Mark in group
                    for g in groups:
                        try:
                            await TournamentStageRepository.update_team_status(session, g.id, m.winner_id, "qualified")
                            await TournamentStageRepository.update_team_status(session, g.id, loser_id, "eliminated")
                        except Exception as e:
                            logger.warning(f"Failed to update team qualification status in group {g.id}: {e}")
        else:
            # Group stage: top N from each group by standings
            for group in groups:
                group_data = await TournamentStageService.get_group_standings(session, group.id)
                standings = group_data["standings"]
                for i, s in enumerate(standings):
                    if i < top_n:
                        await TournamentStageRepository.update_team_status(
                            session, group.id, s["team_id"], "qualified"
                        )
                        qualified_teams.append({
                            "team_id": s["team_id"],
                            "group_rank": i + 1,
                            "group_name": group.group_name,
                        })
                    else:
                        await TournamentStageRepository.update_team_status(
                            session, group.id, s["team_id"], "eliminated"
                        )

        # Check if there's a next stage
        stages = await TournamentStageRepository.get_stages(session, stage.tournament_id)
        current_idx = next((i for i, s in enumerate(stages) if s.id == stage.id), -1)

        if current_idx < 0 or current_idx >= len(stages) - 1:
            # No next stage — leave tournament as "in_progress" for user to add more or complete manually
            await session.commit()
            return

        # Find the correct next stage based on qualified team count
        # Route to the right knockout stage:
        #   5-8 teams → QF (with byes for top seeds if < 8)
        #   3-4 teams → SF
        #   2 teams → Final
        team_count = len(qualified_teams)
        stage_min_teams = {"quarter_final": 5, "semi_final": 3, "final": 2}

        next_stage = None
        for idx in range(current_idx + 1, len(stages)):
            candidate = stages[idx]
            min_needed = stage_min_teams.get(candidate.stage_name, 2)
            if team_count >= min_needed:
                next_stage = candidate
                break
            else:
                # Skip this stage (not enough teams) — mark it completed
                await TournamentStageRepository.update_stage(session, candidate.id, {"status": "completed"})

        if not next_stage:
            await session.commit()
            return

        await TournamentStageRepository.update_stage(session, next_stage.id, {"status": "in_progress"})

        # Create knockout matchups for next stage
        if not tournament:
            tournament = await TournamentRepository.get_by_id(session, stage.tournament_id)

        if next_stage.stage_name in ("quarter_final", "semi_final", "final"):
            pairs = []        # [(team_a_id, team_b_id)] — actual matches
            bye_team_ids = [] # Teams that auto-advance (no opponent)

            if is_knockout_stage:
                # Previous stage was knockout: pair winners sequentially
                for i in range(0, len(qualified_teams) - 1, 2):
                    pairs.append((qualified_teams[i]["team_id"], qualified_teams[i + 1]["team_id"]))
                # Odd winner (if any) gets a bye
                if len(qualified_teams) % 2 == 1:
                    bye_team_ids.append(qualified_teams[-1]["team_id"])
            else:
                # Previous stage was groups
                all_team_ids = [t["team_id"] for t in qualified_teams]

                # If more teams than the stage needs (e.g., 6 for QF=4 slots, or 5-7 for QF),
                # use byes: top seeds advance automatically, rest play QF
                if next_stage.stage_name == "quarter_final" and len(all_team_ids) < 8:
                    # Byes for top seeds: 8 - team_count byes
                    num_byes = 8 - len(all_team_ids)
                    bye_team_ids = all_team_ids[:num_byes]      # Top seeds get byes
                    playing_teams = all_team_ids[num_byes:]      # Rest play QF
                    # Cross-seed the playing teams
                    for i in range(len(playing_teams) // 2):
                        pairs.append((playing_teams[i], playing_teams[len(playing_teams) - 1 - i]))
                else:
                    # Standard cross-seed: winners vs runners-up
                    winners = [t for t in qualified_teams if t.get("group_rank") == 1]
                    runners = [t for t in qualified_teams if t.get("group_rank") == 2]

                    if len(winners) >= 2 and len(runners) >= 2:
                        for i in range(min(len(winners), len(runners))):
                            j = (len(runners) - 1 - i) if len(runners) > 1 else 0
                            pairs.append((winners[i]["team_id"], runners[j]["team_id"]))
                    elif len(qualified_teams) >= 2:
                        for i in range(0, len(qualified_teams) - 1, 2):
                            pairs.append((qualified_teams[i]["team_id"], qualified_teams[i + 1]["team_id"]))

            # If not enough teams for pairs, skip auto-generation (manual setup needed)
            if not pairs:
                await session.commit()
                return

            # Create a single group for the knockout stage
            group = await TournamentStageRepository.create_group(session, {
                "stage_id": next_stage.id,
                "group_name": next_stage.stage_name.replace("_", " ").title(),
                "group_order": 0,
            })

            # Count existing matches for proper numbering (count only)
            count_res = await session.execute(
                select(sa_func.count(MatchSchema.id)).where(MatchSchema.tournament_id == stage.tournament_id)
            )
            match_num = (count_res.scalar() or 0) + 1

            # Stage labels: QF1-QF4, SF1-SF2, Final
            stage_labels = {
                "quarter_final": "QF",
                "semi_final": "SF",
                "final": "Final",
            }
            label_prefix = stage_labels.get(next_stage.stage_name, "M")

            # Create bye matches first (auto-completed walkovers for top seeds)
            for bye_idx, bye_tid in enumerate(bye_team_ids):
                await TournamentStageRepository.add_team_to_group(session, group.id, bye_tid)
                bye_label = f"{label_prefix} {bye_idx + 1} (BYE)"
                await MatchRepository.create(session, {
                    "tournament_id": stage.tournament_id,
                    "team_a_id": bye_tid,
                    "team_b_id": bye_tid,  # Same team = bye indicator
                    "overs": tournament.overs_per_match if tournament else 20,
                    "match_type": next_stage.stage_name,
                    "stage_id": next_stage.id,
                    "group_id": group.id,
                    "match_number": match_num,
                    "time_slot": bye_label,
                    "status": "completed",
                    "result_type": "walkover",
                    "winner_id": bye_tid,
                    "result_summary": f"BYE — auto-advances",
                    "created_by": tournament.created_by if tournament else 1,
                })
                match_num += 1

            # Create actual matches
            actual_match_start = len(bye_team_ids) + 1
            for idx, (ta_id, tb_id) in enumerate(pairs):
                await TournamentStageRepository.add_team_to_group(session, group.id, ta_id)
                await TournamentStageRepository.add_team_to_group(session, group.id, tb_id)
                match_label = f"{label_prefix} {actual_match_start + idx}" if len(pairs) > 1 or bye_team_ids else label_prefix
                await MatchRepository.create(session, {
                    "tournament_id": stage.tournament_id,
                    "team_a_id": ta_id,
                    "team_b_id": tb_id,
                    "overs": tournament.overs_per_match if tournament else 20,
                    "match_type": next_stage.stage_name,
                    "stage_id": next_stage.id,
                    "group_id": group.id,
                    "match_number": match_num,
                    "time_slot": match_label,  # Store label in time_slot for display
                    "created_by": tournament.created_by if tournament else 1,
                })
                match_num += 1

            # 3rd Place Playoff: if current stage is semi_final and tournament has the option
            if stage.stage_name == "semi_final" and tournament and getattr(tournament, 'has_third_place_playoff', False):
                sf_losers = []
                ordered_sf = sorted(stage_matches, key=lambda m: m.match_number or m.id)
                for m in ordered_sf:
                    if m.winner_id:
                        loser = m.team_b_id if m.winner_id == m.team_a_id else m.team_a_id
                        sf_losers.append(loser)
                if len(sf_losers) >= 2:
                    # Create separate 3rd Place stage before Final
                    # Bump final stage order up to make room
                    await TournamentStageRepository.update_stage(session, next_stage.id, {
                        "stage_order": next_stage.stage_order + 1,
                    })
                    tp_stage = await TournamentStageRepository.create_stage(session, {
                        "tournament_id": stage.tournament_id,
                        "stage_name": "third_place",
                        "stage_order": next_stage.stage_order,  # takes old final position
                        "status": "in_progress",
                    })
                    tp_group = await TournamentStageRepository.create_group(session, {
                        "stage_id": tp_stage.id,
                        "group_name": "3rd Place Playoff",
                        "group_order": 0,
                    })
                    await TournamentStageRepository.add_team_to_group(session, tp_group.id, sf_losers[0])
                    await TournamentStageRepository.add_team_to_group(session, tp_group.id, sf_losers[1])
                    await MatchRepository.create(session, {
                        "tournament_id": stage.tournament_id,
                        "team_a_id": sf_losers[0],
                        "team_b_id": sf_losers[1],
                        "overs": tournament.overs_per_match if tournament else 20,
                        "match_type": "third_place",
                        "stage_id": tp_stage.id,
                        "group_id": tp_group.id,
                        "match_number": match_num,
                        "time_slot": "3rd Place",
                        "created_by": tournament.created_by if tournament else 1,
                    })

        await session.commit()
