from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.match_repository import MatchRepository
from src.database.postgres.repositories.innings_repository import InningsRepository
from src.services.undo_service import UndoService


class RevertService:

    @staticmethod
    async def revert_completed_match(session: AsyncSession, match_id: int, user_id: int):
        match = await MatchRepository.get_by_id(session, match_id)
        if not match:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
        if match.status != "completed":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Match is not completed")

        # Reopen match — directly set fields since update() skips None values
        match.status = "live"
        match.winner_id = None
        match.result_summary = None
        match.result_type = None

        innings_list = await InningsRepository.get_by_match(session, match_id)

        if not innings_list:
            # No innings — match was abandoned before scoring started. Just reopen.
            await session.commit()
            return {"message": "Match reverted to live (no innings to restore)"}

        last_innings = innings_list[-1]
        if last_innings.status == "completed":
            await InningsRepository.update(session, last_innings.id, {"status": "live"})

        # Revert "not out" how_out markers set by end_match
        from src.database.postgres.repositories.scorecard_repository import ScorecardRepository
        batting_cards = await ScorecardRepository.get_batting_by_innings(session, last_innings.id)
        for card in batting_cards:
            if not card.is_out and card.how_out == "not out":
                card.how_out = None

        await session.commit()

        from src.database.postgres.repositories.delivery_repository import DeliveryRepository
        last_delivery = await DeliveryRepository.get_last_delivery(session, last_innings.id)
        if last_delivery:
            result = await UndoService.undo_last_ball(session, match_id, user_id)
            return {"message": "Match reverted and last ball undone", **result}

        return {"message": "Match reverted to live"}
