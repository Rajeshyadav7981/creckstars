from fastapi import APIRouter
from src.app.api.routers.auth_router import router as auth_router
from src.app.api.routers.team_router import router as team_router
from src.app.api.routers.player_router import router as player_router
from src.app.api.routers.venue_router import router as venue_router
from src.app.api.routers.tournament_router import router as tournament_router
from src.app.api.routers.match_router import router as match_router
from src.app.api.routers.scoring_router import router as scoring_router
from src.app.api.routers.scorecard_router import router as scorecard_router
from src.app.api.routers.ws_router import router as ws_router
from src.app.api.routers.user_router import router as user_router
from src.app.api.routers.community_router import router as community_router
from src.app.api.routers.notification_router import router as notification_router
from src.app.api.routers.share_router import router as share_router
from src.app.api.routers.telemetry_router import router as telemetry_router

main_router = APIRouter()
main_router.include_router(auth_router)
main_router.include_router(user_router)
main_router.include_router(team_router)
main_router.include_router(player_router)
main_router.include_router(venue_router)
main_router.include_router(tournament_router)
main_router.include_router(match_router)
main_router.include_router(scoring_router)
main_router.include_router(scorecard_router)
main_router.include_router(ws_router)
main_router.include_router(community_router)
main_router.include_router(notification_router)
main_router.include_router(share_router)
main_router.include_router(telemetry_router)
