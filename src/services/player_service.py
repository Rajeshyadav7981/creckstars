from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.player_repository import PlayerRepository
from src.database.postgres.repositories.user_repository import UserRepository


class PlayerService:

    @staticmethod
    async def create_player(session: AsyncSession, user_id: int, first_name: str, last_name: str = None, mobile: str = None, batting_style: str = None, bowling_style: str = None, role: str = None, profile_image: str = None, linked_user_id: int = None, date_of_birth=None, bio: str = None, city: str = None, state_province: str = None, country: str = None):
        full_name = f"{first_name} {last_name}" if last_name else first_name
        data = {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "mobile": mobile,
            "date_of_birth": date_of_birth,
            "bio": bio,
            "city": city,
            "state_province": state_province,
            "country": country,
            "batting_style": batting_style,
            "bowling_style": bowling_style,
            "role": role,
            "profile_image": profile_image,
            "created_by": user_id,
        }
        if linked_user_id:
            data["user_id"] = linked_user_id
        player = await PlayerRepository.create(session, data)
        return player

    @staticmethod
    async def get_or_create_for_user(session: AsyncSession, linked_user_id: int, created_by: int):
        """Find existing player linked to user, or create one from user profile."""
        existing = await PlayerRepository.get_by_user_id(session, linked_user_id)
        if existing:
            return existing
        user = await UserRepository.get_by_id(session, linked_user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return await PlayerRepository.create(session, {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
            "mobile": user.mobile,
            "user_id": linked_user_id,
            "created_by": created_by,
        })

    @staticmethod
    async def get_player(session: AsyncSession, player_id: int):
        player = await PlayerRepository.get_by_id(session, player_id)
        if not player:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
        return player

    @staticmethod
    async def get_players(session: AsyncSession, search: str = None, created_by: int = None, limit: int = 50, offset: int = 0):
        return await PlayerRepository.get_all(session, search=search, created_by=created_by, limit=limit, offset=offset)

    @staticmethod
    async def update_player(session: AsyncSession, player_id: int, data: dict):
        if "first_name" in data or "last_name" in data:
            player = await PlayerRepository.get_by_id(session, player_id)
            if player:
                fn = data.get("first_name", player.first_name)
                ln = data.get("last_name", player.last_name)
                data["full_name"] = f"{fn} {ln}" if ln else fn
        player = await PlayerRepository.update(session, player_id, data)
        if not player:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
        return player
