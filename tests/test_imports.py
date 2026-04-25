"""Every top-level module imports cleanly. Catches 'missing import' regressions
the moment they land, without needing to hit HTTP."""


def test_app_imports():
    from src.app.api.fastapi_app import app  # noqa: F401


def test_services_import():
    from src.services.auth_service import AuthService  # noqa: F401
    from src.services.player_service import PlayerService  # noqa: F401
    from src.services.scorecard_service import ScorecardService  # noqa: F401
    from src.services.scoring_service import ScoringService  # noqa: F401
    from src.services.tournament_service import TournamentService  # noqa: F401
    from src.services.tournament_stage_service import TournamentStageService  # noqa: F401
    from src.services.community_service import CommunityService  # noqa: F401
    from src.services.undo_service import UndoService  # noqa: F401
    from src.services.user_stats_service import UserStatsService  # noqa: F401


def test_repositories_import():
    from src.database.postgres.repositories.user_repository import UserRepository  # noqa: F401
    from src.database.postgres.repositories.player_repository import PlayerRepository  # noqa: F401
    from src.database.postgres.repositories.match_repository import MatchRepository  # noqa: F401


def test_utils_import():
    from src.utils.security import hash_password, verify_password, create_access_token  # noqa: F401
    from src.utils.logger import get_logger  # noqa: F401
    from src.utils.text_parser import validate_username, generate_username  # noqa: F401
