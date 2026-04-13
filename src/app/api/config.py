import os
from dotenv import load_dotenv

# Load .env file from backend root (works for local dev; in production, env vars are set directly)
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), ".env")
load_dotenv(_env_path)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


# ── Environment ──
ENVIRONMENT = _env("ENVIRONMENT", "development")

# ── Database ──
DB_HOST = _env("DB_HOST")
DB_PORT = _env("DB_PORT")
DB_USER = _env("DB_USER")
DB_PASSWORD = _env("DB_PASSWORD")
DB_NAME = _env("DB_NAME")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
ASYNC_DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ── Redis ──
REDIS_URL = _env("REDIS_URL")

# ── JWT / Auth ──
SECRET_KEY = _env("SECRET_KEY")
ALGORITHM = _env("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = _env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 15)
REFRESH_TOKEN_EXPIRE_MINUTES = _env_int("REFRESH_TOKEN_EXPIRE_MINUTES", 10080)

# ── OTP ──
OTP_EXPIRE_MINUTES = _env_int("OTP_EXPIRE_MINUTES", 5)
FAST2SMS_API_KEY = _env("FAST2SMS_MESSAGE_API_KEY")

# ── CORS ──
CORS_ORIGINS = _env("CORS_ORIGINS", "*")

# ── Deep Linking / Share ──
APP_SCHEME = _env("APP_SCHEME", "creckstars")
APP_PACKAGE_NAME = _env("APP_PACKAGE_NAME")
APP_DOWNLOAD_URL = _env("APP_DOWNLOAD_URL")
SHARE_BASE_URL = _env("SHARE_BASE_URL")
APP_SHA256_FINGERPRINT = _env("APP_SHA256_FINGERPRINT")

# ── App Version / APK Hosting ──
# Version info is read from backend/releases/version.json at runtime (no restart needed).
# If version.json doesn't exist, these env vars are used as fallback.
APK_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "releases")
_VERSION_JSON = os.path.join(APK_DIR, "version.json")
APP_LATEST_VERSION = _env("APP_LATEST_VERSION", "1.0.0")
APP_LATEST_VERSION_CODE = _env_int("APP_LATEST_VERSION_CODE", 1)
APP_MIN_VERSION = _env("APP_MIN_VERSION", "1.0.0")
APP_RELEASE_NOTES = _env("APP_RELEASE_NOTES", "Bug fixes and performance improvements")


def get_app_version_info() -> dict:
    """Read version info from releases/version.json (live — no restart needed).
    Falls back to env vars if file doesn't exist."""
    import json
    try:
        with open(_VERSION_JSON, "r") as f:
            data = json.load(f)
        return {
            "latest_version": data.get("latest_version", APP_LATEST_VERSION),
            "latest_version_code": data.get("latest_version_code", APP_LATEST_VERSION_CODE),
            "min_version": data.get("min_version", APP_MIN_VERSION),
            "release_notes": data.get("release_notes", APP_RELEASE_NOTES),
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "latest_version": APP_LATEST_VERSION,
            "latest_version_code": APP_LATEST_VERSION_CODE,
            "min_version": APP_MIN_VERSION,
            "release_notes": APP_RELEASE_NOTES,
        }


# ── Rate Limiting ──
# All values in "requests/period" format. Change in .env to tune limits.
RATE_LIMIT_DEFAULT = _env("RATE_LIMIT_DEFAULT", "200/minute")
RATE_LIMITS = {
    "register":             _env("RATE_LIMIT_REGISTER", "5/minute"),
    "login":                _env("RATE_LIMIT_LOGIN", "10/minute"),
    "score_delivery":       _env("RATE_LIMIT_SCORE", "120/minute"),
    "undo":                 _env("RATE_LIMIT_UNDO", "30/minute"),
    "broadcast":            _env("RATE_LIMIT_BROADCAST", "20/minute"),
    "create_post":          _env("RATE_LIMIT_CREATE_POST", "30/minute"),
    "list_posts":           _env("RATE_LIMIT_LIST_POSTS", "60/minute"),
    "like":                 _env("RATE_LIMIT_LIKE", "60/minute"),
    "comment":              _env("RATE_LIMIT_COMMENT", "30/minute"),
    "list_matches":         _env("RATE_LIMIT_LIST_MATCHES", "60/minute"),
    "list_tournaments":     _env("RATE_LIMIT_LIST_TOURNAMENTS", "60/minute"),
    "create_tournament":    _env("RATE_LIMIT_CREATE_TOURNAMENT", "10/minute"),
    "create_team":          _env("RATE_LIMIT_CREATE_TEAM", "20/minute"),
    "create_match":         _env("RATE_LIMIT_CREATE_MATCH", "20/minute"),
    "create_player":        _env("RATE_LIMIT_CREATE_PLAYER", "30/minute"),
}


# ── Validation ──
def validate_config():
    """Validate configuration and print warnings for insecure/dev settings."""
    import warnings

    is_production = ENVIRONMENT.lower() in ("production", "prod")

    if not SECRET_KEY:
        raise RuntimeError("[CONFIG] FATAL: SECRET_KEY is not set. Add it to .env")

    if SECRET_KEY == "creckstars-secret-change-in-production":
        if is_production:
            raise RuntimeError(
                "[CONFIG] FATAL: SECRET_KEY is set to the default placeholder in production. "
                "Set a strong SECRET_KEY environment variable (at least 32 characters)."
            )
        warnings.warn(
            "[CONFIG] SECRET_KEY is set to the default placeholder. "
            "Set a strong SECRET_KEY in .env for production!",
            stacklevel=2,
        )

    if is_production and len(SECRET_KEY) < 32:
        raise RuntimeError("[CONFIG] FATAL: SECRET_KEY is too short. Use at least 32 characters.")

    if not DB_HOST or not DB_USER or not DB_NAME:
        raise RuntimeError("[CONFIG] Database config incomplete. Check DB_HOST, DB_USER, DB_NAME in .env")

    if CORS_ORIGINS == "*":
        # Mobile apps (React Native) don't use browser CORS — '*' is safe
        warnings.warn("[CONFIG] CORS_ORIGINS is '*'. Fine for mobile APIs, restrict for web.", stacklevel=2)

    if SECRET_KEY == "creckstars-secret-change-in-production" or CORS_ORIGINS == "*":
        print("[CONFIG] WARNING: Running with development-mode settings. Do not use in production.")
