from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.schemas.player_schema import PlayerSchema
from src.database.postgres.schemas.team_schema import TeamSchema
from src.database.postgres.schemas.innings_schema import InningsSchema
from src.database.postgres.schemas.match_schema import MatchSchema
from src.database.postgres.repositories.innings_repository import InningsRepository
from src.database.postgres.repositories.delivery_repository import DeliveryRepository
from src.database.postgres.repositories.scorecard_repository import ScorecardRepository
from src.database.postgres.repositories.match_repository import MatchRepository
from src.database.postgres.repositories.match_event_repository import MatchEventRepository
from src.services.websocket_service import ws_manager
from src.services.cricket_rules import CricketRules
from src.database.redis.match_cache import MatchCache
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ScoringService:

    @staticmethod
    async def record_delivery(session: AsyncSession, match_id: int, user_id: int, data: dict):
        # Lock the match row first so two concurrent requests can't read a stale
        # current_innings. This serialises all mutations for a given match.
        match_res = await session.execute(
            select(MatchSchema).where(MatchSchema.id == match_id).with_for_update()
        )
        match = match_res.scalar_one_or_none()
        if not match or match.status != "live":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Match not live")

        # Lock the innings row too — prevents races if current_innings advances mid-request.
        result = await session.execute(
            select(InningsSchema)
            .where(InningsSchema.match_id == match_id, InningsSchema.innings_number == match.current_innings)
            .with_for_update()
        )
        innings = result.scalar_one_or_none()
        if not innings:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active innings")

        if innings.status != "in_progress":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Innings not in progress")

        # Fetch squad once — try Redis cache first, fall back to DB
        cached_squad = await MatchCache.get_squad(match_id, innings.batting_team_id)
        if cached_squad:
            batting_squad_size = cached_squad["size"]
            batting_squad_ids = cached_squad["ids"]
        else:
            batting_squad = await MatchRepository.get_squad(session, match_id, innings.batting_team_id)
            batting_squad_size = len(batting_squad) if batting_squad else (2 if innings.innings_number > 2 else 11)
            batting_squad_ids = [player.id for player, sq in batting_squad] if batting_squad else []
            await MatchCache.set_squad(match_id, innings.batting_team_id, {"size": batting_squad_size, "ids": batting_squad_ids})

        # Guard: Block scoring if all wickets have fallen
        # Super over: if squad is 2 → 1 wicket ends it; if squad > 2 → 2 wickets max
        if innings.innings_number > 2:
            max_wickets = 1 if batting_squad_size <= 2 else 2
        else:
            max_wickets = max(1, batting_squad_size - 1)
        if innings.total_wickets >= max_wickets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Innings is complete - all wickets have fallen"
            )

        # Guard: Block scoring if all overs have been bowled
        # Super over innings (innings_number > 2) are limited to 1 over
        max_overs = 1 if innings.innings_number > 2 else match.overs
        total_legal_balls_so_far = innings.current_over * 6 + innings.current_ball
        if total_legal_balls_so_far >= max_overs * 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Innings is complete - all overs bowled"
            )

        striker_id = innings.current_striker_id
        non_striker_id = innings.current_non_striker_id
        bowler_id = innings.current_bowler_id

        batsman_runs = data.get("batsman_runs", 0)
        extra_type = data.get("extra_type")
        extra_runs = data.get("extra_runs", 0)
        is_wicket = data.get("is_wicket", False)
        is_boundary = data.get("is_boundary", False)
        is_six = data.get("is_six", False)

        # Input validation
        if extra_type == "wide" and batsman_runs > 0:
            batsman_runs = 0  # Wides cannot have batsman runs

        # Determine if legal delivery
        is_legal = extra_type not in ("wide", "noball")

        # === Cricket Law: Validate dismissal type against delivery type ===
        if is_wicket:
            wicket_type = data.get("wicket_type")
            is_free_hit_legal = innings.is_free_hit and is_legal
            valid, error_msg = CricketRules.validate_wicket_on_extra(extra_type, wicket_type, is_free_hit_legal)
            if not valid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_msg,
                )

        # Total runs for this delivery
        total_runs = CricketRules.calculate_total_runs(batsman_runs, extra_type, extra_runs)

        # Current ball tracking
        current_ball = innings.current_ball
        current_over = innings.current_over
        if is_legal:
            current_ball += 1

        # Get next sequence number
        next_seq = await DeliveryRepository.get_next_ball_seq(session, innings.id)

        # Insert delivery
        # Client-authored commentary (from ShotZonePicker) is persisted if provided.
        delivery_payload = {
            "innings_id": innings.id,
            "over_number": current_over,
            "ball_number": current_ball if is_legal else innings.current_ball,
            "actual_ball_seq": next_seq,
            "striker_id": striker_id,
            "non_striker_id": non_striker_id,
            "bowler_id": bowler_id,
            "batsman_runs": batsman_runs,
            "is_boundary": is_boundary,
            "is_six": is_six,
            "extra_type": extra_type,
            "extra_runs": extra_runs,
            "total_runs": total_runs,
            "is_wicket": is_wicket,
            "wicket_type": data.get("wicket_type"),
            "dismissed_player_id": data.get("dismissed_player_id"),
            "fielder_id": data.get("fielder_id"),
            "is_legal": is_legal,
        }
        client_commentary = data.get("commentary")
        if client_commentary:
            delivery_payload["commentary"] = client_commentary
        delivery = await DeliveryRepository.create(session, delivery_payload)

        # Update batting scorecard (runs scored by batsman, not extras like wides/byes)
        runs_to_batsman = batsman_runs
        if extra_type in ("wide",):
            runs_to_batsman = 0  # wide runs don't go to batsman
        if extra_type in ("bye", "legbye"):
            runs_to_batsman = 0  # byes/legbyes don't go to batsman

        # Bulk-fetch every scorecard row we might touch on this delivery in TWO queries
        # (batting set + bowling set) instead of 3–5 sequential SELECTs. The hot path
        # is ~120 deliveries/min per match, so this is the single biggest win.
        is_wicket_flag = bool(is_wicket)
        dismissed_id_pf = (data.get("dismissed_player_id") or striker_id) if is_wicket_flag else None
        new_batsman_id_pf = data.get("new_batsman_id") if is_wicket_flag else None
        batting_ids_to_fetch = [striker_id]
        if dismissed_id_pf and dismissed_id_pf not in batting_ids_to_fetch:
            batting_ids_to_fetch.append(dismissed_id_pf)
        if new_batsman_id_pf and new_batsman_id_pf not in batting_ids_to_fetch:
            batting_ids_to_fetch.append(new_batsman_id_pf)

        batting_map = await ScorecardRepository.get_batting_cards_for_players(
            session, innings.id, batting_ids_to_fetch
        )
        bowling_map = await ScorecardRepository.get_bowling_cards_for_players(
            session, innings.id, [bowler_id]
        )

        batting_card = await ScorecardRepository.ensure_batting_card(
            session, innings.id, striker_id, batting_map.get(striker_id)
        )
        new_runs = batting_card.runs + runs_to_batsman
        # Ball faced: legal deliveries + no-balls count (batsman faces the ball).
        # Only wides don't count as ball faced (batsman didn't play it).
        balls_faced_increment = 1 if (is_legal or extra_type == "noball") else 0
        new_balls = batting_card.balls_faced + balls_faced_increment
        new_fours = batting_card.fours + (1 if is_boundary and not is_six else 0)
        new_sixes = batting_card.sixes + (1 if is_six else 0)
        new_sr = (new_runs / new_balls * 100) if new_balls > 0 else 0.0
        batting_card.runs = new_runs
        batting_card.balls_faced = new_balls
        batting_card.fours = new_fours
        batting_card.sixes = new_sixes
        batting_card.strike_rate = round(new_sr, 2)

        # Update bowling scorecard
        # Leg byes and byes are extras NOT charged to the bowler
        # Wides and no-balls ARE charged to the bowler
        bowling_card = await ScorecardRepository.ensure_bowling_card(
            session, innings.id, bowler_id, bowling_map.get(bowler_id)
        )
        if extra_type in ("bye", "legbye"):
            bowl_runs = 0  # Byes/leg byes don't count against bowler
        else:
            bowl_runs = total_runs  # Wides, no-balls, and normal runs count
        bowl_wides = bowling_card.wides + (1 if extra_type == "wide" else 0)
        bowl_nb = bowling_card.no_balls + (1 if extra_type == "noball" else 0)
        bowl_dots = bowling_card.dot_balls + (1 if total_runs == 0 and is_legal else 0)
        bowl_wickets = bowling_card.wickets + (1 if is_wicket and data.get("wicket_type") not in ("run_out",) else 0)
        new_bowl_runs = bowling_card.runs_conceded + bowl_runs
        # Calculate overs bowled
        legal_balls_total = int(bowling_card.overs_bowled) * 6 + round((bowling_card.overs_bowled % 1) * 10)
        if is_legal:
            legal_balls_total += 1
        new_overs = legal_balls_total // 6 + (legal_balls_total % 6) / 10
        new_economy = (new_bowl_runs / (legal_balls_total / 6)) if legal_balls_total > 0 else 0.0

        bowling_card.runs_conceded = new_bowl_runs
        bowling_card.overs_bowled = round(new_overs, 1)
        bowling_card.wides = bowl_wides
        bowling_card.no_balls = bowl_nb
        bowling_card.dot_balls = bowl_dots
        bowling_card.wickets = bowl_wickets
        bowling_card.economy_rate = round(new_economy, 2)

        # Update partnership
        partnership = await ScorecardRepository.get_active_partnership(session, innings.id)
        if partnership:
            partnership.total_runs += total_runs
            partnership.total_balls += (1 if is_legal else 0)
            if extra_type in ("wide", "bye", "legbye"):
                partnership.extras += total_runs
            elif partnership.player_a_id == striker_id:
                partnership.player_a_runs += runs_to_batsman
            else:
                partnership.player_b_runs += runs_to_batsman

        # Update over record
        over_record = await InningsRepository.get_over(session, innings.id, current_over)
        if over_record:
            over_record.runs_conceded += total_runs
            over_record.wickets += (1 if is_wicket else 0)
            over_record.wides += (1 if extra_type == "wide" else 0)
            over_record.no_balls += (1 if extra_type == "noball" else 0)

        # Handle wicket
        if is_wicket:
            dismissed_id = data.get("dismissed_player_id") or striker_id
            batting_card_dismissed = await ScorecardRepository.ensure_batting_card(
                session, innings.id, dismissed_id, batting_map.get(dismissed_id)
            )
            wicket_type = data.get("wicket_type", "out")
            fielder_id = data.get("fielder_id")

            # Build descriptive how_out string with player names
            # Batch-load all needed players in one query to avoid N+1
            player_ids_needed = [pid for pid in (bowler_id, fielder_id) if pid]
            players_map = {}
            if player_ids_needed:
                result = await session.execute(
                    select(PlayerSchema).where(PlayerSchema.id.in_(player_ids_needed))
                )
                for p in result.scalars().all():
                    players_map[p.id] = p
            bowler_player = players_map.get(bowler_id)
            fielder_player = players_map.get(fielder_id)
            bowler_name = bowler_player.full_name if bowler_player else ""
            fielder_name = fielder_player.full_name if fielder_player else ""

            out_desc = CricketRules.format_how_out(wicket_type, bowler_name, fielder_name, None)

            batting_card_dismissed.is_out = True
            batting_card_dismissed.how_out = out_desc
            batting_card_dismissed.bowler_id = bowler_id if wicket_type != "run_out" else None
            batting_card_dismissed.fielder_id = fielder_id

            # Fall of wicket
            await ScorecardRepository.add_fall_of_wicket(session, {
                "innings_id": innings.id,
                "wicket_number": innings.total_wickets + 1,
                "player_id": dismissed_id,
                "runs_at_fall": innings.total_runs + total_runs,
                "overs_at_fall": round(current_over + current_ball / 10, 1) if is_legal else round(current_over + innings.current_ball / 10, 1),
                "delivery_id": delivery.id,
            })

            # End partnership
            if partnership:
                partnership.is_active = False

            # Set new batsman if provided
            new_batsman_id = data.get("new_batsman_id")
            if new_batsman_id:
                # Validate new batsman is in the batting team's squad (reuse cached squad)
                if new_batsman_id not in batting_squad_ids:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="New batsman must be from the batting team's squad"
                    )
                # Validate new batsman is not already dismissed (reuse the prefetched card).
                existing_card = await ScorecardRepository.ensure_batting_card(
                    session, innings.id, new_batsman_id, batting_map.get(new_batsman_id)
                )
                if existing_card.is_out:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Cannot select a dismissed player as new batsman"
                    )

                # Start new partnership
                surviving_batsman = non_striker_id if dismissed_id == striker_id else striker_id
                await ScorecardRepository.get_or_create_partnership(
                    session, innings.id, innings.total_wickets + 2,
                    surviving_batsman, new_batsman_id,
                )

        # Update innings totals
        new_total_runs = innings.total_runs + total_runs
        new_total_wickets = innings.total_wickets + (1 if is_wicket else 0)
        new_total_extras = innings.total_extras + (total_runs - runs_to_batsman)
        total_legal = current_over * 6 + (current_ball if is_legal else innings.current_ball)
        new_total_overs = total_legal // 6 + (total_legal % 6) / 10

        innings_update = {
            "total_runs": new_total_runs,
            "total_wickets": new_total_wickets,
            "total_overs": round(new_total_overs, 1),
            "total_extras": new_total_extras,
            "current_ball": current_ball if is_legal else innings.current_ball,
            "current_over": current_over,
        }

        # Track free hit state: next delivery after a no-ball is a free hit
        if extra_type == "noball":
            innings_update["is_free_hit"] = True
        elif is_legal:
            innings_update["is_free_hit"] = False

        # Handle new batsman and strike swaps
        # Cricket law: determine strike swap first, then place new batsman
        # in the correct (already-swapped) position to avoid double-swapping.
        should_swap = CricketRules.should_swap_strike(batsman_runs, extra_type, extra_runs)

        if is_wicket:
            new_batsman_id = data.get("new_batsman_id")
            dismissed_id = data.get("dismissed_player_id") or striker_id

            # Start with current positions
            pos_striker = striker_id
            pos_non_striker = non_striker_id

            # Step 1: Apply strike swap to surviving batsman
            if should_swap:
                pos_striker, pos_non_striker = pos_non_striker, pos_striker

            # Step 2: Place new batsman in the dismissed player's position
            if new_batsman_id:
                if dismissed_id == pos_striker:
                    pos_striker = new_batsman_id
                else:
                    pos_non_striker = new_batsman_id
                innings_update["current_striker_id"] = pos_striker
                innings_update["current_non_striker_id"] = pos_non_striker
        else:
            # Normal strike swap per cricket laws
            if should_swap:
                innings_update["current_striker_id"] = non_striker_id
                innings_update["current_non_striker_id"] = striker_id

        await InningsRepository.update(session, innings.id, innings_update)

        # Check if over complete (6 legal balls)
        over_complete = is_legal and current_ball >= 6

        # Check if innings complete
        innings_complete = False
        if new_total_wickets >= max_wickets:
            innings_complete = True
        if is_legal and current_over * 6 + current_ball >= max_overs * 6:
            innings_complete = True
        if innings.target and new_total_runs >= innings.target:
            innings_complete = True

        # Store match event
        seq = await MatchEventRepository.get_next_sequence(session, match_id)
        await MatchEventRepository.create(session, {
            "match_id": match_id,
            "event_type": "delivery",
            "event_data": {
                "delivery_id": delivery.id,
                "batsman_runs": batsman_runs,
                "extra_type": extra_type,
                "extra_runs": extra_runs,
                "total_runs": total_runs,
                "is_wicket": is_wicket,
                "is_legal": is_legal,
                "over_complete": over_complete,
                "innings_complete": innings_complete,
            },
            "match_state": {
                "total_runs": new_total_runs,
                "total_wickets": new_total_wickets,
                "total_overs": round(new_total_overs, 1),
                "current_over": current_over,
                "current_ball": current_ball if is_legal else innings.current_ball,
                "striker_id": innings_update.get("current_striker_id", striker_id),
                "non_striker_id": innings_update.get("current_non_striker_id", non_striker_id),
                "bowler_id": bowler_id,
                "is_free_hit": innings_update.get("is_free_hit", False),
            },
            "sequence_number": seq,
            "created_by": user_id,
        })

        # Auto-complete innings if all overs/wickets/target done.
        #
        # IMPORTANT: we mark only the *innings* as completed here. We do NOT
        # auto-finalize the *match* even when the chase target is reached.
        # That would create asymmetric UX:
        #   • all-out / overs-done in 2nd innings → match.status='live' →
        #     scorer sees the End Match popup on reopen
        #   • chase target reached → match.status='completed' →
        #     scorer can't reach the End Match popup on reopen
        #
        # By keeping match.status='live' until end_match is explicitly called,
        # both flows go through the same "innings_break" path and the scorer
        # always gets to confirm or undo via the InningsEndDialog. The
        # tournament stage progression also defers until the scorer confirms,
        # which avoids premature notifications on a wrong winning ball.
        if innings_complete:
            await InningsRepository.update(session, innings.id, {"status": "completed"})
            # Mark batsmen as not out
            batting_cards = await ScorecardRepository.get_batting_by_innings(session, innings.id)
            for card in batting_cards:
                if not card.is_out and card.how_out is None:
                    card.how_out = "not out"
            await session.flush()

        try:
            await session.commit()
        except IntegrityError as ie:
            # Hit the ux_deliveries_innings_seq guard — concurrent writer raced us.
            # Roll back and ask the client to retry rather than returning 500.
            await session.rollback()
            logger.warning(f"Duplicate ball seq on match {match_id}: {ie.orig}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another delivery is being recorded — retry in a moment.",
            )

        logger.info(f"Delivery: match={match_id} runs={total_runs} wicket={is_wicket} overs={new_total_overs}")

        # Broadcast via WebSocket
        result = {
            "delivery_id": delivery.id,
            "total_runs": total_runs,
            "innings_runs": new_total_runs,
            "innings_wickets": new_total_wickets,
            "innings_overs": round(new_total_overs, 1),
            "over_complete": over_complete,
            "innings_complete": innings_complete,
            "is_legal": is_legal,
            # Event flags — viewers use these to trigger celebration overlays
            "batsman_runs": batsman_runs,
            "is_wicket": is_wicket,
            "is_six": is_six,
            "is_boundary": is_boundary,
            "wicket_type": data.get("wicket_type") if is_wicket else None,
            # Client-authored enrichments (optional) — pass through for live commentary / wagon wheel
            "commentary": data.get("commentary"),
            "field_zone": data.get("field_zone"),
            "batting_hand": data.get("batting_hand"),
        }
        await ws_manager.broadcast(match_id, {"type": "delivery", "data": result})

        return result

    @staticmethod
    async def end_over(session: AsyncSession, match_id: int, next_bowler_id: int):
        match_res = await session.execute(
            select(MatchSchema).where(MatchSchema.id == match_id).with_for_update()
        )
        match = match_res.scalar_one_or_none()
        if not match or match.status != "live":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Match not live")

        innings_list = await InningsRepository.get_by_match(session, match_id, match.current_innings)
        innings = innings_list[0]

        # Guard: Super over innings are limited to 1 over — do not allow starting a new over
        if innings.innings_number > 2:
            max_overs = 1
            total_legal_balls = innings.current_over * 6 + innings.current_ball
            if total_legal_balls >= max_overs * 6:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Super over innings complete — cannot start new over"
                )

        # Guard: Do not allow end_over on a completed innings
        if innings.status == "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Innings already completed"
            )

        # Guard: Same bowler cannot bowl consecutive overs
        if innings.current_bowler_id == next_bowler_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Same bowler cannot bowl consecutive overs"
            )

        # Calculate the correct new over from total_overs (more reliable than current_over)
        new_over = int(innings.total_overs) if innings.total_overs else innings.current_over + 1

        # Check maiden on current over
        over_record = await InningsRepository.get_over(session, innings.id, innings.current_over)
        if over_record and over_record.runs_conceded == 0:
            over_record.is_maiden = True
            bowling_card = await ScorecardRepository.get_or_create_bowling(session, innings.id, over_record.bowler_id)
            bowling_card.maidens += 1

        # Swap strike at end of over
        striker = innings.current_striker_id
        non_striker = innings.current_non_striker_id

        # Create new over (get or create to handle duplicate)
        existing_over = await InningsRepository.get_over(session, innings.id, new_over)
        if not existing_over:
            await InningsRepository.create_over(session, {
                "innings_id": innings.id,
                "over_number": new_over,
                "bowler_id": next_bowler_id,
            })
        else:
            existing_over.bowler_id = next_bowler_id
        await ScorecardRepository.get_or_create_bowling(session, innings.id, next_bowler_id)

        await InningsRepository.update(session, innings.id, {
            "current_over": new_over,
            "current_ball": 0,
            "current_bowler_id": next_bowler_id,
            "current_striker_id": non_striker,
            "current_non_striker_id": striker,
        })

        await session.commit()
        logger.info(f"Over ended: match={match_id} new_over={new_over}")
        result = {"over": new_over, "bowler_id": next_bowler_id}
        await ws_manager.broadcast(match_id, {"type": "over_end", "data": result})
        return result

    @staticmethod
    async def end_innings(session: AsyncSession, match_id: int):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match or match.status != "live":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Match not live")

        # Find the current active innings
        current_inn = match.current_innings
        if not current_inn:
            # Find the latest non-completed innings
            all_innings = await InningsRepository.get_by_match(session, match_id)
            active = [i for i in all_innings if i.status != "completed"]
            if not active:
                return {"message": "All innings already completed", "total_runs": 0}
            innings = active[0]
        else:
            innings_list = await InningsRepository.get_by_match(session, match_id, current_inn)
            if not innings_list:
                return {"message": "No innings found", "total_runs": 0}
            innings = innings_list[0]

        # Skip if already completed
        if innings.status == "completed":
            return {"message": f"Innings {innings.innings_number} already completed", "total_runs": innings.total_runs}

        # Mark current batsmen as "not out"
        batting_cards = await ScorecardRepository.get_batting_by_innings(session, innings.id)
        for card in batting_cards:
            if not card.is_out and card.how_out is None:
                card.how_out = "not out"
        await session.flush()

        await InningsRepository.update(session, innings.id, {"status": "completed"})
        await session.commit()

        logger.info(f"Innings ended: match={match_id}")

        result = {
            "message": f"Innings {innings.innings_number} completed",
            "total_runs": innings.total_runs,
        }
        await ws_manager.broadcast(match_id, {"type": "innings_end", "data": result})
        return result

    @staticmethod
    async def end_match(session: AsyncSession, match_id: int, force_tie: bool = False):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")

        innings_list = await InningsRepository.get_by_match(session, match_id)

        # Get team names for result summary
        team_a = await session.get(TeamSchema, match.team_a_id)
        team_b = await session.get(TeamSchema, match.team_b_id)
        team_names = {
            match.team_a_id: team_a.name if team_a else "Team A",
            match.team_b_id: team_b.name if team_b else "Team B",
        }

        winner_id = None
        result_summary = "Match ended"

        if len(innings_list) < 2:
            pass
        else:
            # Compare the latest completed pair of innings
            # Regular: innings 1 & 2, Super over: innings 3 & 4, etc.
            last_pair = innings_list[-2:]
            inn_a = last_pair[0]
            inn_b = last_pair[1]

            if inn_b.total_runs > inn_a.total_runs:
                winner_id = inn_b.batting_team_id
                if inn_b.innings_number > 2:
                    result_summary = f"{team_names.get(inn_b.batting_team_id, 'Team')} won in Super Over"
                else:
                    wickets_remaining = 10 - inn_b.total_wickets
                    result_summary = f"{team_names.get(inn_b.batting_team_id, 'Team')} won by {wickets_remaining} wickets"
            elif inn_a.total_runs > inn_b.total_runs:
                winner_id = inn_a.batting_team_id
                if inn_a.innings_number > 2:
                    result_summary = f"{team_names.get(inn_a.batting_team_id, 'Team')} won in Super Over"
                else:
                    runs_diff = inn_a.total_runs - inn_b.total_runs
                    result_summary = f"{team_names.get(inn_a.batting_team_id, 'Team')} won by {runs_diff} runs"
            else:
                # Tied — offer super over unless force_tie
                if not force_tie:
                    so_label = "Super Over tied" if inn_a.innings_number > 2 else "Match tied"
                    return {
                        "is_tied": True,
                        "result_summary": so_label,
                        "team_a_runs": inn_a.total_runs,
                        "team_b_runs": inn_b.total_runs,
                    }
                winner_id = None
                result_summary = "Match tied"

        await MatchRepository.update(session, match_id, {
            "status": "completed",
            "winner_id": winner_id,
            "result_summary": result_summary,
        })

        # Mark all innings as completed and finalize batting scorecards
        for inn in innings_list:
            if inn.status != "completed":
                await InningsRepository.update(session, inn.id, {"status": "completed"})
            batting_cards = await ScorecardRepository.get_batting_by_innings(session, inn.id)
            for card in batting_cards:
                if not card.is_out and card.how_out is None:
                    card.how_out = "not out"
        await session.flush()
        await session.commit()

        logger.info(f"Match ended: match={match_id} winner={winner_id} result={result_summary}")

        # Trigger stage progression check (updates standings, qualifications, next stage)
        from src.services.tournament_stage_service import TournamentStageService
        await TournamentStageService.on_match_completed(session, match_id)

        result = {"winner_id": winner_id, "result_summary": result_summary}
        await ws_manager.broadcast(match_id, {"type": "match_end", "data": result})
        return result

    @staticmethod
    async def swap_batters(session: AsyncSession, match_id: int):
        """Manually swap striker and non-striker. Not a cricket rule — admin option."""
        match_res = await session.execute(
            select(MatchSchema).where(MatchSchema.id == match_id).with_for_update()
        )
        match = match_res.scalar_one_or_none()
        if not match or match.status != "live":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Match not live")

        result = await session.execute(
            select(InningsSchema)
            .where(InningsSchema.match_id == match_id, InningsSchema.innings_number == match.current_innings)
            .with_for_update()
        )
        innings = result.scalar_one_or_none()
        if not innings or innings.status != "in_progress":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Innings not in progress")

        if not innings.current_striker_id or not innings.current_non_striker_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Both batsmen must be set")

        await InningsRepository.update(session, innings.id, {
            "current_striker_id": innings.current_non_striker_id,
            "current_non_striker_id": innings.current_striker_id,
        })
        await session.commit()
        await ws_manager.broadcast(match_id, {"type": "delivery", "data": {"swap": True}})
        return {"message": "Batters swapped"}
