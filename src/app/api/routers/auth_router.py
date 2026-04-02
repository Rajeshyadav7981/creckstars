import os
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt
from src.database.postgres.db import get_async_db
from src.services.auth_service import AuthService
from src.utils.security import get_current_user, create_access_token, create_refresh_token
from src.app.api.config import SECRET_KEY, ALGORITHM
from src.database.postgres.repositories.user_repository import UserRepository
from src.app.api.rate_limiter import limiter
from src.app.api.config import RATE_LIMITS
from src.app.api.routers.models.auth_model import (
    RegisterRequest,
    LoginRequest,
    UpdateProfileRequest,
    UserResponse,
)

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "uploads")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
@limiter.limit(RATE_LIMITS["register"])
async def register(
    request: Request,
    data: RegisterRequest,
    session: AsyncSession = Depends(get_async_db),
):
    """Register user. OTP verified by Firebase on frontend."""
    return await AuthService.register(
        session=session,
        first_name=data.first_name,
        last_name=data.last_name,
        mobile=data.mobile,
        email=data.email,
        password=data.password,
        profile=data.profile,
        username=data.username,
    )


@router.post("/login")
@limiter.limit(RATE_LIMITS["login"])
async def login(
    request: Request,
    data: LoginRequest,
    session: AsyncSession = Depends(get_async_db),
):
    """Login with password. OTP verified by Firebase on frontend."""
    return await AuthService.login(
        session=session,
        mobile=data.mobile,
        password=data.password,
    )


@router.post("/refresh")
async def refresh_token(request: Request, session: AsyncSession = Depends(get_async_db)):
    """Exchange refresh token for new access + refresh tokens."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing refresh token")
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = await UserRepository.get_by_id(session, int(user_id))
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        new_access = create_access_token({"sub": str(user.id)})
        new_refresh = create_refresh_token(user.id)
        return {"access_token": new_access, "refresh_token": new_refresh, "token_type": "bearer"}
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expired or invalid")


@router.get("/me", response_model=UserResponse)
async def get_me(current_user=Depends(get_current_user)):
    """Get current authenticated user."""
    return current_user


@router.put("/me", response_model=UserResponse)
async def update_me(
    data: UpdateProfileRequest,
    current_user=Depends(get_current_user),
    session: AsyncSession = Depends(get_async_db),
):
    """Update current user's profile."""
    return await AuthService.update_profile(
        session=session,
        user_id=current_user.id,
        data=data.model_dump(exclude_unset=True),
    )


@router.post("/me/upload-photo")
async def upload_profile_photo(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    session: AsyncSession = Depends(get_async_db),
):
    """Upload profile photo and update user's profile field."""
    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="File type not allowed. Use JPG, PNG, GIF, or WebP.")

    # Read and validate size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max 5 MB.")

    # Save file
    profiles_dir = os.path.join(UPLOADS_DIR, "profiles")
    os.makedirs(profiles_dir, exist_ok=True)
    filename = f"{current_user.id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(profiles_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    # Update user profile field with relative URL
    profile_url = f"/uploads/profiles/{filename}"
    await UserRepository.update_user(session, current_user.id, {"profile": profile_url})

    # Invalidate Redis user cache so stale photo URL is not served
    try:
        from src.database.redis.redis_client import redis_client
        r = await redis_client.get_client()
        if r:
            await r.delete(f"user:{current_user.id}")
    except Exception:
        pass

    return {"profile": profile_url}
