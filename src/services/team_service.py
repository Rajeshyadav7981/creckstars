import random
import string
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.repositories.team_repository import TeamRepository
from src.database.postgres.repositories.player_repository import PlayerRepository


def _generate_team_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "T" + "".join(random.choices(chars, k=5))


class TeamService:

    @staticmethod
    async def create_team(session: AsyncSession, name: str, user_id: int, short_name: str = None, logo_url: str = None, color: str = None, home_ground: str = None, city: str = None, latitude: float = None, longitude: float = None):
        # Auto-generate unique team code
        team_code = None
        for _ in range(10):
            code = _generate_team_code()
            existing = await TeamRepository.get_by_code(session, code)
            if not existing:
                team_code = code
                break
        if not team_code:
            raise HTTPException(status_code=500, detail="Failed to generate unique team code")

        team = await TeamRepository.create(session, {
            "team_code": team_code,
            "name": name,
            "short_name": short_name,
            "logo_url": logo_url,
            "color": color,
            "home_ground": home_ground,
            "city": city,
            "latitude": latitude,
            "longitude": longitude,
            "created_by": user_id,
        })
        return team

    @staticmethod
    async def get_team(session: AsyncSession, team_id: int):
        team = await TeamRepository.get_by_id(session, team_id)
        if not team:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        return team

    @staticmethod
    async def get_teams(
        session: AsyncSession, created_by: int = None,
        search: str = None, code: str = None,
        lat: float = None, lng: float = None,
        limit: int = 50, offset: int = 0,
    ):
        return await TeamRepository.get_all(
            session, created_by=created_by, search=search, code=code,
            lat=lat, lng=lng, limit=limit, offset=offset,
        )

    @staticmethod
    async def get_team_detail(session: AsyncSession, team_id: int):
        team = await TeamRepository.get_by_id(session, team_id)
        if not team:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        rows = await TeamRepository.get_team_players(session, team_id)
        players = []
        for player, tp in rows:
            players.append({
                "player_id": player.id,
                "first_name": player.first_name,
                "last_name": player.last_name,
                "full_name": player.full_name,
                "role": player.role,
                "jersey_number": tp.jersey_number,
                "is_captain": tp.is_captain,
                "is_vice_captain": getattr(tp, 'is_vice_captain', False) or False,
                "is_wicket_keeper": tp.is_wicket_keeper,
            })
        return {
            "team": {
                "id": team.id,
                "team_code": team.team_code,
                "name": team.name,
                "short_name": team.short_name,
                "logo_url": team.logo_url,
                "color": team.color,
                "home_ground": team.home_ground,
                "created_by": team.created_by,
            },
            "players": players,
        }

    @staticmethod
    def _check_owner(team, user_id: int):
        if team.created_by != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the team creator can perform this action")

    @staticmethod
    async def add_player(session: AsyncSession, team_id: int, player_id: int, jersey_number: int = None,
                         is_captain: bool = False, is_vice_captain: bool = False, is_wicket_keeper: bool = False,
                         user_id: int = None):
        team = await TeamRepository.get_by_id(session, team_id)
        if not team:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        if user_id:
            TeamService._check_owner(team, user_id)
        player = await PlayerRepository.get_by_id(session, player_id)
        if not player:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
        # If setting as captain, unset existing captain
        if is_captain:
            await TeamRepository.unset_role(session, team_id, "is_captain")
        if is_vice_captain:
            await TeamRepository.unset_role(session, team_id, "is_vice_captain")
        try:
            return await TeamRepository.add_player(session, {
                "team_id": team_id, "player_id": player_id,
                "jersey_number": jersey_number, "is_captain": is_captain,
                "is_vice_captain": is_vice_captain, "is_wicket_keeper": is_wicket_keeper,
            })
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Player already in team")

    @staticmethod
    async def update_player_role(session: AsyncSession, team_id: int, player_id: int, updates: dict, user_id: int = None):
        """Update captain/vice-captain/WK status. Automatically unsets previous holder."""
        team = await TeamRepository.get_by_id(session, team_id)
        if not team:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        if user_id:
            TeamService._check_owner(team, user_id)
        # Unset previous holder if setting a new one
        if updates.get("is_captain") is True:
            await TeamRepository.unset_role(session, team_id, "is_captain")
        if updates.get("is_vice_captain") is True:
            await TeamRepository.unset_role(session, team_id, "is_vice_captain")
        return await TeamRepository.update_player(session, team_id, player_id, updates)

    @staticmethod
    async def remove_player(session: AsyncSession, team_id: int, player_id: int, user_id: int = None):
        if user_id:
            team = await TeamRepository.get_by_id(session, team_id)
            if not team:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
            TeamService._check_owner(team, user_id)
        removed = await TeamRepository.remove_player(session, team_id, player_id)
        if not removed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not in team")
        return {"message": "Player removed from team"}
