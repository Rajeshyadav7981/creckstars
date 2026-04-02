import random
import string
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.match_repository import MatchRepository
from src.database.postgres.repositories.innings_repository import InningsRepository
from src.database.postgres.repositories.scorecard_repository import ScorecardRepository
from src.database.postgres.repositories.match_event_repository import MatchEventRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _generate_code(prefix: str = "M") -> str:
    chars = string.ascii_uppercase + string.digits
    return prefix + "".join(random.choices(chars, k=6))


class MatchService:

    @staticmethod
    def _check_owner(match, user_id: int):
        if match.created_by != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the match creator can perform this action")

    @staticmethod
    async def create_match(session: AsyncSession, user_id: int, **kwargs):
        kwargs["created_by"] = user_id
        kwargs["scorer_user_id"] = user_id
        # Auto-generate unique match code
        for _ in range(10):
            code = _generate_code("M")
            existing = await MatchRepository.get_by_code(session, code)
            if not existing:
                kwargs["match_code"] = code
                break
        return await MatchRepository.create(session, kwargs)

    @staticmethod
    async def get_match(session: AsyncSession, match_id: int):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
        return match

    @staticmethod
    async def get_matches(
        session: AsyncSession, status_filter: str = None, tournament_id: int = None,
        search: str = None, created_by: int = None, limit: int = 50, offset: int = 0,
    ):
        return await MatchRepository.get_all(
            session, status=status_filter, tournament_id=tournament_id,
            search=search, created_by=created_by, limit=limit, offset=offset,
        )

    @staticmethod
    async def record_toss(session: AsyncSession, match_id: int, toss_winner_id: int, toss_decision: str, user_id: int = None):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
        if user_id:
            MatchService._check_owner(match, user_id)
        if match.status in ("live", "completed"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change toss after match has started")
        if toss_decision not in ("bat", "bowl"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Toss decision must be 'bat' or 'bowl'")
        if toss_winner_id not in (match.team_a_id, match.team_b_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Toss winner must be one of the match teams")
        return await MatchRepository.update(session, match_id, {
            "toss_winner_id": toss_winner_id,
            "toss_decision": toss_decision,
            "status": "toss",
        })

    @staticmethod
    async def set_squad(session: AsyncSession, match_id: int, team_id: int, players: list, user_id: int = None):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
        if user_id:
            MatchService._check_owner(match, user_id)
        if match.status in ("live", "completed"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change squad after match has started")
        if team_id not in (match.team_a_id, match.team_b_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Team not in this match")
        entries = []
        for p in players:
            entries.append({
                "match_id": match_id,
                "team_id": team_id,
                "player_id": p["player_id"],
                "is_playing": True,
                "batting_order": p.get("batting_order"),
            })
        return await MatchRepository.set_squad(session, entries)

    @staticmethod
    async def start_innings(session: AsyncSession, match_id: int, batting_team_id: int, striker_id: int, non_striker_id: int, bowler_id: int, user_id: int = None):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
        if user_id:
            MatchService._check_owner(match, user_id)

        innings_number = (match.current_innings or 0) + 1
        # Regular match: max 2 innings. Super over adds innings 3, 4, 5, 6, etc.
        if innings_number > 2 and innings_number % 2 == 1:
            # Odd super over innings (3, 5, 7) - this is the first SO innings of a pair, OK
            pass
        elif innings_number > 2 and innings_number % 2 == 0:
            # Even super over innings (4, 6, 8) - second SO innings of a pair, OK
            pass
        elif innings_number > 2:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Both innings already played")

        bowling_team_id = match.team_b_id if batting_team_id == match.team_a_id else match.team_a_id

        # Determine target
        target = None
        if innings_number == 2:
            # 2nd innings: target = 1st innings runs + 1
            first_innings = await InningsRepository.get_by_match(session, match_id, 1)
            if first_innings:
                target = first_innings[0].total_runs + 1
        elif innings_number > 2 and innings_number % 2 == 0:
            # Even super over innings: target = previous SO innings runs + 1
            prev_innings = await InningsRepository.get_by_match(session, match_id, innings_number - 1)
            if prev_innings:
                target = prev_innings[0].total_runs + 1

        innings = await InningsRepository.create(session, {
            "match_id": match_id,
            "innings_number": innings_number,
            "batting_team_id": batting_team_id,
            "bowling_team_id": bowling_team_id,
            "status": "in_progress",
            "target": target,
            "current_striker_id": striker_id,
            "current_non_striker_id": non_striker_id,
            "current_bowler_id": bowler_id,
        })

        # Create batting scorecards for openers
        await ScorecardRepository.get_or_create_batting(session, innings.id, striker_id, position=1)
        await ScorecardRepository.get_or_create_batting(session, innings.id, non_striker_id, position=2)
        # Create bowling scorecard for opening bowler
        await ScorecardRepository.get_or_create_bowling(session, innings.id, bowler_id)
        # Create first over
        await InningsRepository.create_over(session, {
            "innings_id": innings.id,
            "over_number": 0,
            "bowler_id": bowler_id,
        })
        # Create opening partnership
        await ScorecardRepository.get_or_create_partnership(session, innings.id, 0, striker_id, non_striker_id)

        # Update match status
        await MatchRepository.update(session, match_id, {
            "status": "live",
            "current_innings": innings_number,
        })

        await session.commit()

        # Auto-subscribe creator + squad players for push notifications (1st innings only)
        if innings_number == 1:
            try:
                from src.services.notification_service import NotificationService
                import asyncio
                asyncio.create_task(NotificationService.auto_subscribe_match_participants(match_id))
            except Exception as e:
                logger.warning(f"Failed to auto-subscribe match participants for match {match_id}: {e}")

        return innings

    @staticmethod
    async def get_squad(session: AsyncSession, match_id: int, team_id: int):
        rows = await MatchRepository.get_squad(session, match_id, team_id)
        result = []
        for player, sq in rows:
            result.append({
                "player_id": player.id,
                "full_name": player.full_name,
                "role": player.role,
                "batting_order": sq.batting_order,
                "is_playing": sq.is_playing,
            })
        return result
    
