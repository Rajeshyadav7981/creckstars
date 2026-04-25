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
from src.app.api.config import RATE_LIMITS, OTP_BYPASS_ENABLED
from src.app.api.routers.models.auth_model import (
    RegisterRequest,
    LoginRequest,
    UpdateProfileRequest,
    UserResponse,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "uploads")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
# Accept up to 10 MB; the compressor below re-encodes to ≤ 2 MB on disk.
MAX_FILE_SIZE = 10 * 1024 * 1024

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
@limiter.limit(RATE_LIMITS["register"])
async def register(
    request: Request,
    data: RegisterRequest,
    session: AsyncSession = Depends(get_async_db),
):
    """Register user. OTP must be verified before calling this endpoint."""
    return await AuthService.register(
        session=session,
        first_name=data.first_name,
        last_name=data.last_name,
        mobile=data.mobile,
        email=data.email,
        password=data.password,
        profile=data.profile,
        username=data.username,
        bio=data.bio,
        city=data.city,
        state_province=data.state_province,
        country=data.country,
        date_of_birth=data.date_of_birth,
        batting_style=data.batting_style,
        bowling_style=data.bowling_style,
        player_role=data.player_role,
    )


@router.post("/login")
@limiter.limit(RATE_LIMITS["login"])
async def login(
    request: Request,
    data: LoginRequest,
    session: AsyncSession = Depends(get_async_db),
):
    """Login with password. OTP must be verified before calling this endpoint."""
    return await AuthService.login(
        session=session,
        mobile=data.mobile,
        password=data.password,
    )


@router.post("/refresh")
@limiter.limit(RATE_LIMITS["refresh"])
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
    """Upload profile photo and update user's profile field.

    Security posture:
      - Ignore user-supplied filename entirely — only UUID + server-chosen extension
        land on disk, so ``../`` / null bytes / long-names can't escape the uploads dir.
      - Decode the image with Pillow; if decoding fails we reject it. This also
        strips EXIF and normalises output to JPEG.
      - Enforce size cap on the decoded bytes.
    """
    import io
    from PIL import Image as PILImage, UnidentifiedImageError

    # Cheap client-declared content-type gate (defence in depth; Pillow is authoritative).
    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Max 10 MB.")

    # Authoritative validation: can Pillow open it as an image?
    try:
        img = PILImage.open(io.BytesIO(content))
        img.verify()  # verify integrity without decoding fully
        img = PILImage.open(io.BytesIO(content))  # reopen; verify() exhausts file
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(status_code=400, detail="File is not a valid image.")

    # Re-encode to ≤ 2 MB. Avatar is a 1024-wide square; the compressor will
    # step quality down and shrink if a high-res upload still overruns.
    from src.utils.image_compress import compress_to_target_size
    content = compress_to_target_size(img, target_bytes=2 * 1024 * 1024, max_width=1024)

    # Save file — filename is server-generated only; no user input on disk.
    profiles_dir = os.path.join(UPLOADS_DIR, "profiles")
    os.makedirs(profiles_dir, exist_ok=True)
    filename = f"{current_user.id}_{uuid.uuid4().hex}.jpg"
    filepath = os.path.join(profiles_dir, filename)
    # Defence in depth: ensure the resolved path is still inside profiles_dir.
    resolved = os.path.realpath(filepath)
    if not resolved.startswith(os.path.realpath(profiles_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid upload path.")
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
    except Exception as _e:
        logger.warning(
            "User cache invalidate failed after avatar upload",
            extra={"extra_data": {"user_id": current_user.id, "error": str(_e)}},
        )

    return {"profile": profile_url}


# ── OTP Verification ──

class SendOTPRequest(BaseModel):
    mobile: str
    purpose: str = "register"  # "register" | "login" | "reset_password"

class VerifyOTPRequest(BaseModel):
    mobile: str
    otp: str
    purpose: str = "register"

class ResetPasswordRequest(BaseModel):
    mobile: str
    otp: str
    new_password: str


def _mask_mobile(mobile: str) -> str:
    """Return a privacy-safe prefix for logging (never log full mobile or OTP code)."""
    if not mobile:
        return "?"
    return f"{mobile[:3]}****{mobile[-2:]}" if len(mobile) >= 5 else mobile[:2] + "***"


async def _otp_lockout_check(mobile: str, purpose: str):
    """Raise 429 if this mobile has exceeded the OTP verify attempt budget.
    Uses Redis with a TTL equal to OTP_LOCKOUT_MINUTES so lockout self-heals.
    """
    from src.app.api.config import OTP_MAX_ATTEMPTS, OTP_LOCKOUT_MINUTES
    from src.database.redis.redis_client import redis_client
    try:
        r = await redis_client.get_client()
        if not r:
            return  # Redis down — fail open rather than locking everyone out
        key = f"otp_fails:{mobile}:{purpose}"
        count = await r.get(key)
        if count and int(count) >= OTP_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail=f"Too many wrong attempts. Try again in {OTP_LOCKOUT_MINUTES} minutes.",
            )
    except HTTPException:
        raise
    except Exception:
        # Redis transient issue — do not brick the endpoint
        return


async def _otp_record_failure(mobile: str, purpose: str):
    from src.app.api.config import OTP_LOCKOUT_MINUTES
    from src.database.redis.redis_client import redis_client
    try:
        r = await redis_client.get_client()
        if not r:
            return
        key = f"otp_fails:{mobile}:{purpose}"
        n = await r.incr(key)
        if n == 1:
            await r.expire(key, OTP_LOCKOUT_MINUTES * 60)
    except Exception:
        return


async def _otp_clear_failures(mobile: str, purpose: str):
    from src.database.redis.redis_client import redis_client
    try:
        r = await redis_client.get_client()
        if not r:
            return
        await r.delete(f"otp_fails:{mobile}:{purpose}")
    except Exception:
        return


@router.post("/send-otp")
@limiter.limit(RATE_LIMITS["send_otp"])
async def send_otp(
    request: Request,
    data: SendOTPRequest,
    session: AsyncSession = Depends(get_async_db),
):
    """Send OTP to mobile number."""
    from datetime import datetime, timedelta, timezone
    from src.database.postgres.repositories.otp_repository import OTPRepository
    from src.utils.security import generate_otp
    from src.app.api.config import OTP_EXPIRE_MINUTES

    mobile = data.mobile.strip()
    if len(mobile) != 10 or not mobile.isdigit():
        raise HTTPException(status_code=400, detail="Invalid mobile number")

    # Don't let a locked-out account re-arm itself by requesting a new OTP.
    await _otp_lockout_check(mobile, data.purpose)

    # For password reset, the mobile must already be registered
    if data.purpose == "reset_password":
        existing = await UserRepository.get_by_mobile(session, mobile)
        if not existing:
            raise HTTPException(status_code=404, detail="No account found with this mobile number")

    otp_code = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRE_MINUTES)

    await OTPRepository.create_otp(session, {
        "mobile": mobile,
        "otp_code": otp_code,
        "purpose": data.purpose,
        "expires_at": expires_at,
    })

    # Send OTP via MSG91
    from src.app.api.config import SMS_API_KEY, SMS_TEMPLATE_ID, ENVIRONMENT
    import httpx

    if SMS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://control.msg91.com/api/v5/otp",
                    headers={
                        "authkey": SMS_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={
                        "mobile": f"91{mobile}",
                        "otp": otp_code,
                        "otp_length": 6,
                        "otp_expiry": OTP_EXPIRE_MINUTES,
                        "template_id": SMS_TEMPLATE_ID,
                    },
                )
                result = resp.json()
                if result.get("type") == "error":
                    logger.warning(
                        "MSG91 rejected OTP send",
                        extra={"extra_data": {"mobile": _mask_mobile(mobile), "msg91": result.get("message")}},
                    )
                    raise HTTPException(status_code=500, detail="SMS service could not send. Try again.")
                if result.get("type") != "success":
                    logger.warning(
                        "MSG91 unexpected response",
                        extra={"extra_data": {"mobile": _mask_mobile(mobile), "msg91_type": result.get("type")}},
                    )
                logger.info(
                    "OTP dispatched",
                    extra={"extra_data": {"mobile": _mask_mobile(mobile), "purpose": data.purpose}},
                )
        except httpx.HTTPError as e:
            logger.error(
                "MSG91 request failed",
                extra={"extra_data": {"mobile": _mask_mobile(mobile), "error": str(e)}},
            )
            raise HTTPException(status_code=500, detail="SMS service unavailable. Try again.")

    # Dev-only: log OTP code for local testing. Never in production; never full mobile.
    if ENVIRONMENT.lower() not in ("production", "prod"):
        logger.info(
            "Dev OTP generated",
            extra={"extra_data": {"mobile": _mask_mobile(mobile), "otp": otp_code, "purpose": data.purpose}},
        )

    return {"message": "OTP sent", "expires_in": OTP_EXPIRE_MINUTES * 60}


@router.post("/verify-otp")
@limiter.limit(RATE_LIMITS["verify_otp"])
async def verify_otp(
    request: Request,
    data: VerifyOTPRequest,
    session: AsyncSession = Depends(get_async_db),
):
    """Verify OTP code. Enforces per-mobile lockout after repeated wrong guesses."""
    from datetime import datetime, timezone
    from src.database.postgres.repositories.otp_repository import OTPRepository

    mobile = data.mobile.strip()
    otp_input = data.otp.strip()

    # DLT-pending dev bypass: accept any 6-digit numeric OTP. Flip
    # OTP_BYPASS_ENABLED=false once real SMS delivery is wired up.
    if OTP_BYPASS_ENABLED and len(otp_input) == 6 and otp_input.isdigit():
        logger.warning(f"[OTP BYPASS] accepting dummy OTP for {mobile} (purpose={data.purpose})")
        await _otp_clear_failures(mobile, data.purpose)
        existing = await OTPRepository.get_latest_otp(session, mobile, data.purpose)
        if existing and not existing.is_verified:
            await OTPRepository.mark_verified(session, existing.id)
        return {"verified": True}

    await _otp_lockout_check(mobile, data.purpose)

    otp_record = await OTPRepository.get_latest_otp(session, mobile, data.purpose)

    if not otp_record:
        raise HTTPException(status_code=400, detail="No OTP found. Request a new one.")

    if otp_record.is_verified:
        raise HTTPException(status_code=400, detail="OTP already used")

    if otp_record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OTP expired. Request a new one.")

    # Constant-time compare to avoid timing leaks
    import secrets as _secrets
    if not _secrets.compare_digest(otp_record.otp_code, data.otp.strip()):
        await _otp_record_failure(mobile, data.purpose)
        # Delete the record so the code can't be re-guessed after rotation.
        await OTPRepository.delete_otp(session, otp_record.id)
        raise HTTPException(status_code=400, detail="Invalid OTP")

    await OTPRepository.mark_verified(session, otp_record.id)
    await _otp_clear_failures(mobile, data.purpose)
    return {"verified": True}


@router.post("/reset-password")
@limiter.limit(RATE_LIMITS["reset_password"])
async def reset_password(
    request: Request,
    data: ResetPasswordRequest,
    session: AsyncSession = Depends(get_async_db),
):
    """Reset password after OTP verification.
    Flow: user calls /send-otp with purpose='reset_password' → enters OTP + new password here.
    """
    from datetime import datetime, timezone
    import secrets as _secrets
    from src.database.postgres.repositories.otp_repository import OTPRepository
    from src.utils.security import hash_password

    mobile = data.mobile.strip()
    new_password = data.new_password
    otp_input = data.otp.strip()

    # Password strength check (same rules as register)
    if len(new_password) < 8 or len(new_password) > 50:
        raise HTTPException(status_code=400, detail="Password must be 8–50 characters")
    if not any(c.isalpha() for c in new_password):
        raise HTTPException(status_code=400, detail="Password must contain a letter")
    if not any(c.isdigit() for c in new_password):
        raise HTTPException(status_code=400, detail="Password must contain a number")

    # Verify user exists (same gate regardless of OTP path)
    user = await UserRepository.get_by_mobile(session, mobile)
    if not user:
        raise HTTPException(status_code=404, detail="No account found with this mobile number")

    bypass = OTP_BYPASS_ENABLED and len(otp_input) == 6 and otp_input.isdigit()
    if bypass:
        logger.warning(f"[OTP BYPASS] accepting dummy OTP for reset_password {mobile}")
        await _otp_clear_failures(mobile, "reset_password")
        existing = await OTPRepository.get_latest_otp(session, mobile, "reset_password")
        if existing and not existing.is_verified:
            await OTPRepository.mark_verified(session, existing.id)
    else:
        await _otp_lockout_check(mobile, "reset_password")
        # Validate the reset OTP (same logic as verify-otp, with attempt tracking)
        otp_record = await OTPRepository.get_latest_otp(session, mobile, "reset_password")
        if not otp_record:
            raise HTTPException(status_code=400, detail="No OTP found. Request a new one.")
        if otp_record.is_verified:
            raise HTTPException(status_code=400, detail="OTP already used. Request a new one.")
        if otp_record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="OTP expired. Request a new one.")
        if not _secrets.compare_digest(otp_record.otp_code, otp_input):
            await _otp_record_failure(mobile, "reset_password")
            await OTPRepository.delete_otp(session, otp_record.id)
            raise HTTPException(status_code=400, detail="Invalid OTP")
        # Mark OTP used and clear lockout counter
        await OTPRepository.mark_verified(session, otp_record.id)
        await _otp_clear_failures(mobile, "reset_password")

    # Update password
    await UserRepository.update_password(session, user.id, hash_password(new_password))

    try:
        from src.database.redis.redis_client import redis_client
        r = await redis_client.get_client()
        if r:
            await r.delete(f"user:{user.id}")
    except Exception as _e:
        logger.warning("User cache invalidate failed", extra={"extra_data": {"user_id": user.id, "error": str(_e)}})

    logger.info(
        "Password reset",
        extra={"extra_data": {"user_id": user.id, "mobile": _mask_mobile(mobile)}},
    )
    return {"message": "Password reset successful. Please log in with your new password."}
