import secrets
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from src.app.api.config import SECRET_KEY, ALGORITHM, REFRESH_TOKEN_EXPIRE_MINUTES
from src.database.postgres.db import get_async_db
from src.database.postgres.repositories.user_repository import UserRepository


@dataclass
class CachedUser:
    """Typed shape for the Redis user-cache payload.

    Using an explicit dataclass instead of `setattr` on a dynamic object stops
    a tampered Redis payload from injecting arbitrary attributes (e.g.
    overwriting `is_admin`) onto the request's current-user object.

    IMPORTANT: keep in sync with the fields serialized by
    ``_serialize_user_for_cache`` below and consumed by ``UserResponse``.
    Missing a field here (e.g. followers_count) silently defaults it on every
    cache hit, which is why clients see stale 0s.
    """
    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    profile: Any = None
    bio: str | None = None
    city: str | None = None
    state_province: str | None = None
    country: str | None = None
    date_of_birth: Any = None  # ISO date string; Pydantic parses on response
    batting_style: str | None = None
    bowling_style: str | None = None
    player_role: str | None = None
    followers_count: int = 0
    following_count: int = 0
    created_at: Any = None

    @classmethod
    def from_cache(cls, data: dict) -> "CachedUser":
        # Whitelist via explicit field set — arbitrary keys in `data` are ignored.
        return cls(**{k: data.get(k) for k in _CACHE_FIELDS if k in data})


# Single source of truth for what we round-trip through Redis.
_CACHE_FIELDS = (
    "id", "username", "first_name", "last_name", "full_name",
    "mobile", "email", "profile",
    "bio", "city", "state_province", "country", "date_of_birth",
    "batting_style", "bowling_style", "player_role",
    "followers_count", "following_count", "created_at",
)


def _serialize_user_for_cache(user) -> dict:
    """Pull the whitelisted fields off an ORM user into a JSON-safe dict."""
    out: dict[str, Any] = {}
    for k in _CACHE_FIELDS:
        v = getattr(user, k, None)
        # dates / datetimes → ISO strings so json.dumps doesn't blow up.
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[k] = v
    # Normalise counts so a cache hit never returns None for int fields.
    out["followers_count"] = int(out.get("followers_count") or 0)
    out["following_count"] = int(out.get("following_count") or 0)
    return out

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against bcrypt hash. Also supports legacy SHA-256 for migration."""
    try:
        if bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8")):
            return True
    except (ValueError, TypeError):
        pass
    # Fallback: legacy SHA-256 check for existing users
    if hashlib.sha256(plain_password.encode("utf-8")).hexdigest() == hashed_password:
        return True
    return False


def needs_rehash(hashed_password: str) -> bool:
    """Check if a password hash needs to be upgraded from SHA-256 to bcrypt."""
    # bcrypt hashes start with $2b$ or $2a$
    if hashed_password.startswith(("$2b$", "$2a$")):
        return False
    return True


def generate_otp() -> str:
    """Generate a cryptographically strong 6-digit OTP."""
    # secrets.randbelow is CSPRNG-backed; zero-pad so codes < 100000 still 6 digits
    return f"{secrets.randbelow(1_000_000):06d}"


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=30))
    to_encode.update({
        "exp": expire,
        "type": "access",
        "jti": uuid.uuid4().hex,  # Unique token ID for revocation support
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    """Create a refresh token with longer expiry."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": str(user_id),
        "exp": expire,
        "type": "refresh",
        "jti": uuid.uuid4().hex,  # Unique token ID for revocation support
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_async_db),
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            raise credentials_exception
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        user_id = int(sub)
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    import json as _json

    # Cache user lookup in Redis (avoids DB query on every API call).
    # Payload is coerced through CachedUser's whitelist — arbitrary keys in the
    # cache blob cannot land as attributes on the request user object.
    try:
        from src.database.redis.redis_client import redis_client
        r = await redis_client.get_client()
        if r:
            cached_user = await r.get(f"user:{user_id}")
            if cached_user:
                return CachedUser.from_cache(_json.loads(cached_user))
    except Exception:
        pass

    user = await UserRepository.get_by_id(session, user_id)
    if user is None:
        raise credentials_exception

    try:
        r = await redis_client.get_client()
        if r:
            # Full payload so cache hits can satisfy UserResponse without
            # defaulting followers_count / counts / cricket-profile fields to
            # zero / None. TTL 5 min; invalidated explicitly on profile edit,
            # photo upload, follow/unfollow.
            await r.setex(f"user:{user_id}", 300, _json.dumps(_serialize_user_for_cache(user)))
    except Exception:
        pass

    return user


async def get_current_user_optional(
    token: str | None = Depends(oauth2_scheme_optional),
    session: AsyncSession = Depends(get_async_db),
):
    """Anonymous-friendly auth dependency.

    Returns None when no token is supplied. If a token IS supplied but fails
    to validate, we propagate the 401 rather than silently falling back to
    anonymous — otherwise a tampered/expired token is indistinguishable from
    no token on the server, and clients can't tell their session has died.
    """
    if not token:
        return None
    return await get_current_user(token=token, session=session)
