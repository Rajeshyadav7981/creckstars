import random
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from src.app.api.config import SECRET_KEY, ALGORITHM, REFRESH_TOKEN_EXPIRE_MINUTES
from src.database.postgres.db import get_async_db
from src.database.postgres.repositories.user_repository import UserRepository

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against bcrypt hash. Also supports legacy SHA-256 for migration."""
    # Try bcrypt first
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
    """Generate a 6-digit OTP."""
    return str(random.randint(100000, 999999))


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

    # Cache user lookup in Redis (avoids DB query on every API call)
    try:
        from src.database.redis.redis_client import redis_client
        r = await redis_client.get_client()
        if r:
            cached_user = await r.get(f"user:{user_id}")
            if cached_user:
                data = _json.loads(cached_user)
                # Return a simple object with needed attributes
                class CachedUser:
                    pass
                u = CachedUser()
                for k, v in data.items():
                    setattr(u, k, v)
                return u
    except Exception:
        pass

    user = await UserRepository.get_by_id(session, user_id)
    if user is None:
        raise credentials_exception

    # Cache for 5 minutes
    try:
        r = await redis_client.get_client()
        if r:
            await r.setex(f"user:{user_id}", 300, _json.dumps({
                "id": user.id, "username": getattr(user, 'username', None),
                "first_name": user.first_name, "last_name": user.last_name,
                "full_name": user.full_name, "mobile": user.mobile, "email": user.email,
                "profile": user.profile,
            }))
    except Exception:
        pass

    return user


async def get_current_user_optional(
    token: str | None = Depends(oauth2_scheme_optional),
    session: AsyncSession = Depends(get_async_db),
):
    """Like get_current_user, but returns None if no token or invalid token.
    Use for public endpoints that personalize output when signed in."""
    if not token:
        return None
    try:
        return await get_current_user(token=token, session=session)
    except HTTPException:
        return None
