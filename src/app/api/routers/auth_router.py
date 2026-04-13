import os
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
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

    # Compress if > 2MB or not JPEG
    COMPRESS_THRESHOLD = 2 * 1024 * 1024
    if len(content) > COMPRESS_THRESHOLD or ext not in (".jpg", ".jpeg"):
        try:
            from PIL import Image as PILImage
            import io
            img = PILImage.open(io.BytesIO(content))
            img = img.convert("RGB")
            # Resize if too large (max 1024px wide)
            if img.width > 1024:
                ratio = 1024 / img.width
                img = img.resize((1024, int(img.height * ratio)), PILImage.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75, optimize=True)
            content = buf.getvalue()
            ext = ".jpg"
        except Exception:
            pass  # Keep original if compression fails

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


# ── OTP Verification ──

class SendOTPRequest(BaseModel):
    mobile: str
    purpose: str = "register"  # "register" or "login"

class VerifyOTPRequest(BaseModel):
    mobile: str
    otp: str
    purpose: str = "register"


@router.post("/send-otp")
@limiter.limit("5/minute")
async def send_otp(
    request: Request,
    data: SendOTPRequest,
    session: AsyncSession = Depends(get_async_db),
):
    """Send OTP to mobile number. Free — logs OTP to console for testing."""
    import random
    from datetime import datetime, timedelta, timezone
    from src.database.postgres.repositories.otp_repository import OTPRepository

    mobile = data.mobile.strip()
    if len(mobile) != 10 or not mobile.isdigit():
        raise HTTPException(status_code=400, detail="Invalid mobile number")

    otp_code = str(random.randint(100000, 999999))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    await OTPRepository.create_otp(session, {
        "mobile": mobile,
        "otp_code": otp_code,
        "purpose": data.purpose,
        "expires_at": expires_at,
    })

    # Send OTP via Fast2SMS
    from src.app.api.config import FAST2SMS_API_KEY
    import httpx

    if FAST2SMS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://www.fast2sms.com/dev/bulkV2",
                    params={
                        "authorization": FAST2SMS_API_KEY,
                        "variables_values": otp_code,
                        "route": "otp",
                        "numbers": mobile,
                    },
                )
                result = resp.json()
                if not result.get("return"):
                    print(f"[OTP] Fast2SMS error: {result.get('message')}")
                    raise HTTPException(status_code=500, detail="Failed to send OTP. Try again.")
        except httpx.HTTPError as e:
            print(f"[OTP] Fast2SMS request failed: {e}")
            raise HTTPException(status_code=500, detail="SMS service unavailable. Try again.")
    else:
        # Fallback for local dev — log to console
        print(f"[OTP] {mobile}: {otp_code} (purpose: {data.purpose})")

    return {"message": "OTP sent", "expires_in": 300}


@router.post("/verify-otp")
@limiter.limit("10/minute")
async def verify_otp(
    request: Request,
    data: VerifyOTPRequest,
    session: AsyncSession = Depends(get_async_db),
):
    """Verify OTP code."""
    from datetime import datetime, timezone
    from src.database.postgres.repositories.otp_repository import OTPRepository

    mobile = data.mobile.strip()
    otp_record = await OTPRepository.get_latest_otp(session, mobile, data.purpose)

    if not otp_record:
        raise HTTPException(status_code=400, detail="No OTP found. Request a new one.")

    if otp_record.is_verified:
        raise HTTPException(status_code=400, detail="OTP already used")

    if otp_record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OTP expired. Request a new one.")

    if otp_record.otp_code != data.otp.strip():
        raise HTTPException(status_code=400, detail="Invalid OTP")

    await OTPRepository.mark_verified(session, otp_record.id)
    return {"verified": True}
