from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.match_event_repository import MatchEventRepository
from src.database.postgres.repositories.delivery_repository import DeliveryRepository
from src.database.postgres.repositories.innings_repository import InningsRepository
from src.database.postgres.repositories.match_repository import MatchRepository
from src.database.postgres.repositories.scorecard_repository import ScorecardRepository


class UndoService:

    @staticmethod
    async def undo_last_ball(session: AsyncSession, match_id: int, user_id: int):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match or match.status != "live":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Match not live")

        # Get last event
        last_event = await MatchEventRepository.get_last_event(session, match_id)
        if not last_event or last_event.event_type != "delivery":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No delivery to undo")

        event_data = last_event.event_data or {}
        delivery_id = event_data.get("delivery_id")

        # Fetch delivery before deleting (we need its data to revert scorecards)
        delivery = None
        if delivery_id:
            delivery = await DeliveryRepository.get_by_id(session, delivery_id)

        # Revert scorecard entries using the delivery data
        innings_list = await InningsRepository.get_by_match(session, match_id, match.current_innings)
        if not innings_list:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active innings")
        innings = innings_list[0]

        if delivery:
            # Revert batting scorecard
            striker_id = delivery.striker_id
            batsman_runs = delivery.batsman_runs or 0
            extra_type = delivery.extra_type
            is_legal = delivery.is_legal
            is_wicket = delivery.is_wicket
            is_boundary = delivery.is_boundary
            is_six = delivery.is_six

            runs_to_batsman = batsman_runs
            if extra_type in ("wide", "bye", "legbye"):
                runs_to_batsman = 0

            batting_card = await ScorecardRepository.get_or_create_batting(session, innings.id, striker_id)
            batting_card.runs = max(0, batting_card.runs - runs_to_batsman)
            # Ball faced: legal + no-ball count, only wide doesn't
            balls_faced_decrement = 1 if (is_legal or extra_type == "noball") else 0
            batting_card.balls_faced = max(0, batting_card.balls_faced - balls_faced_decrement)
            batting_card.fours = max(0, batting_card.fours - (1 if is_boundary and not is_six else 0))
            batting_card.sixes = max(0, batting_card.sixes - (1 if is_six else 0))
            batting_card.strike_rate = round((batting_card.runs / batting_card.balls_faced * 100) if batting_card.balls_faced > 0 else 0.0, 2)

            # Revert bowling scorecard
            # Byes/leg byes are NOT charged to the bowler (same rule as scoring)
            bowler_id = delivery.bowler_id
            bowling_card = await ScorecardRepository.get_or_create_bowling(session, innings.id, bowler_id)
            total_runs = delivery.total_runs or 0
            bowl_runs_to_revert = 0 if extra_type in ("bye", "legbye") else total_runs
            bowling_card.runs_conceded = max(0, bowling_card.runs_conceded - bowl_runs_to_revert)
            bowling_card.dot_balls = max(0, bowling_card.dot_balls - (1 if total_runs == 0 and is_legal else 0))
            bowling_card.wides = max(0, bowling_card.wides - (1 if extra_type == "wide" else 0))
            bowling_card.no_balls = max(0, bowling_card.no_balls - (1 if extra_type == "noball" else 0))
            if is_wicket and delivery.wicket_type not in ("run_out",):
                bowling_card.wickets = max(0, bowling_card.wickets - 1)

            # Recalculate bowling overs
            legal_balls = int(bowling_card.overs_bowled) * 6 + round((bowling_card.overs_bowled % 1) * 10)
            if is_legal:
                legal_balls = max(0, legal_balls - 1)
            bowling_card.overs_bowled = round(legal_balls // 6 + (legal_balls % 6) / 10, 1)
            bowling_card.economy_rate = round((bowling_card.runs_conceded / (legal_balls / 6)) if legal_balls > 0 else 0.0, 2)

            # Revert over record
            over_record = await InningsRepository.get_over(session, innings.id, delivery.over_number)
            if over_record:
                over_record.runs_conceded = max(0, over_record.runs_conceded - total_runs)
                over_record.wickets = max(0, over_record.wickets - (1 if is_wicket else 0))
                over_record.wides = max(0, over_record.wides - (1 if extra_type == "wide" else 0))
                over_record.no_balls = max(0, over_record.no_balls - (1 if extra_type == "noball" else 0))

            # Revert wicket-related entries
            if is_wicket:
                dismissed_id = delivery.dismissed_player_id or striker_id
                # Un-dismiss the batsman
                dismissed_card = await ScorecardRepository.get_or_create_batting(session, innings.id, dismissed_id)
                dismissed_card.is_out = False
                dismissed_card.how_out = None
                dismissed_card.bowler_id = None
                dismissed_card.fielder_id = None

                # Remove fall of wicket for this delivery
                await ScorecardRepository.remove_fall_of_wicket_by_delivery(session, innings.id, delivery_id)

                # Reactivate old partnership, deactivate new one
                await ScorecardRepository.revert_partnership_for_wicket(session, innings.id)

        # Mark event as undone
        await MatchEventRepository.mark_undone(session, last_event.id)

        # Delete the delivery
        if delivery_id:
            await DeliveryRepository.delete(session, delivery_id)

        # Get the event before this one to restore state
        prev_event = await MatchEventRepository.get_last_event(session, match_id)

        if prev_event and prev_event.match_state:
            state = prev_event.match_state
            await InningsRepository.update(session, innings.id, {
                "total_runs": state.get("total_runs", 0),
                "total_wickets": state.get("total_wickets", 0),
                "total_overs": state.get("total_overs", 0.0),
                "current_over": state.get("current_over", 0),
                "current_ball": state.get("current_ball", 0),
                "current_striker_id": state.get("striker_id"),
                "current_non_striker_id": state.get("non_striker_id"),
                "current_bowler_id": state.get("bowler_id"),
                "is_free_hit": state.get("is_free_hit", False),
                "status": "in_progress",  # Always revert to in_progress on undo
            })
        else:
            # First ball undone - reset innings to initial state
            await InningsRepository.update(session, innings.id, {
                "total_runs": 0,
                "total_wickets": 0,
                "total_overs": 0.0,
                "current_ball": 0,
                "is_free_hit": False,
                "status": "in_progress",
            })

        # Also revert match status if it was auto-completed
        if match.status == "completed":
            await MatchRepository.update(session, match_id, {
                "status": "live",
                "winner_id": None,
                "result_summary": None,
                "result_type": None,
            })

        # Store undo event
        seq = await MatchEventRepository.get_next_sequence(session, match_id)
        await MatchEventRepository.create(session, {
            "match_id": match_id,
            "event_type": "undo",
            "event_data": {"undone_event_id": last_event.id, "undone_delivery_id": delivery_id},
            "match_state": prev_event.match_state if prev_event else {},
            "sequence_number": seq,
            "created_by": user_id,
        })

        await session.commit()
        return {"message": "Last delivery undone", "undone_delivery_id": delivery_id}
