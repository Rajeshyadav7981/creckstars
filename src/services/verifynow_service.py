"""Message Central VerifyNow — managed OTP verification (no DLT required).

Flow (the provider owns the OTP + template; we only hold a verificationId):

    1. GET  /auth/v1/authentication/token   -> authToken      (cached in Redis)
    2. POST /verification/v3/send            -> verificationId (stashed in Redis per mobile+purpose)
    3. GET  /verification/v3/validateOtp/    -> verificationStatus == VERIFICATION_COMPLETED

Docs: https://www.messagecentral.com/en-in/product/verify-now/api-india

The token is cached and auto-refreshed on a 401. The verificationId is stored
in Redis keyed by (mobile, purpose) with the same TTL as a local OTP, so the
verify step can look it up without a DB column.
"""
import base64
import httpx

from src.utils.logger import get_logger
from src.utils.http_client import get_http_client
from src.app.api.config import (
    MC_BASE_URL, MC_CUSTOMER_ID, MC_PASSWORD, MC_COUNTRY_CODE,
    MC_FLOW_TYPE, OTP_EXPIRE_MINUTES,
)
from src.database.redis.redis_client import redis_client

logger = get_logger(__name__)

_TOKEN_KEY = "verifynow:token"
_TOKEN_TTL = 25 * 60  # refresh well before MC's token expiry; 401 also forces refresh


def _vid_key(mobile: str, purpose: str) -> str:
    return f"verifynow:vid:{mobile}:{purpose}"


class VerifyNowError(Exception):
    """Raised when VerifyNow returns an unexpected/failed response on send.

    response_code carries VerifyNow's numeric responseCode (e.g. "506" =
    REQUEST_ALREADY_EXISTS) so callers can map it to a useful HTTP status.
    """

    def __init__(self, message: str, response_code: str | None = None):
        super().__init__(message)
        self.response_code = response_code


class VerifyNowService:

    @staticmethod
    async def _get_token(*, force_refresh: bool = False) -> str:
        r = await redis_client.get_client()
        if r and not force_refresh:
            cached = await r.get(_TOKEN_KEY)
            if cached:
                return cached

        key_b64 = base64.b64encode(MC_PASSWORD.encode()).decode()
        resp = await get_http_client().get(
            f"{MC_BASE_URL}/auth/v1/authentication/token",
            params={
                "customerId": MC_CUSTOMER_ID,
                "key": key_b64,
                "scope": "NEW",
                "country": MC_COUNTRY_CODE,
            },
            timeout=10,
        )
        try:
            data = resp.json()
        except ValueError:
            raise VerifyNowError(f"token endpoint returned non-JSON (HTTP {resp.status_code})")
        token = data.get("token")
        if not token:
            raise VerifyNowError(f"token fetch failed: {data}")
        if r:
            await r.set(_TOKEN_KEY, token, ex=_TOKEN_TTL)
        return token

    @staticmethod
    async def send(mobile: str, purpose: str) -> tuple[str, int]:
        """Ask VerifyNow to generate + deliver an OTP.

        Returns (verificationId, timeout_seconds). timeout_seconds is VerifyNow's
        own OTP validity (typically 60s) so the API can hand the UI an accurate
        expiry countdown instead of guessing OTP_EXPIRE_MINUTES.
        """
        async def _do(tok: str) -> httpx.Response:
            return await get_http_client().post(
                f"{MC_BASE_URL}/verification/v3/send",
                params={
                    "countryCode": MC_COUNTRY_CODE,
                    "flowType": MC_FLOW_TYPE,
                    "mobileNumber": mobile,
                    "otpLength": 6,
                },
                headers={"authToken": tok},
                timeout=10,
            )

        token = await VerifyNowService._get_token()
        resp = await _do(token)
        if resp.status_code == 401:
            resp = await _do(await VerifyNowService._get_token(force_refresh=True))

        try:
            data = resp.json()
        except ValueError:
            raise VerifyNowError(f"send returned non-JSON (HTTP {resp.status_code})")
        rc = str(data.get("responseCode"))
        if rc != "200":
            raise VerifyNowError(f"send rejected: {data.get('message')} ({rc})", response_code=rc)

        vid = str(data.get("data", {}).get("verificationId") or "")
        if not vid:
            raise VerifyNowError(f"send ok but no verificationId: {data}")

        # VerifyNow's own validity window (e.g. "60.0"). Fall back to 60s.
        try:
            timeout = int(float(data.get("data", {}).get("timeout") or 60))
        except (TypeError, ValueError):
            timeout = 60

        r = await redis_client.get_client()
        if r:
            # Keep the verificationId at least as long as VerifyNow's own window.
            ttl = max(timeout, OTP_EXPIRE_MINUTES * 60)
            await r.set(_vid_key(mobile, purpose), vid, ex=ttl)
        return vid, timeout

    @staticmethod
    async def validate(mobile: str, purpose: str, code: str) -> bool:
        """Validate a user-entered code against the stored verificationId.

        Returns True only on VERIFICATION_COMPLETED. Any provider error or
        missing/expired verificationId returns False (treated as a wrong code
        by the caller, which records the failed attempt).
        """
        r = await redis_client.get_client()
        vid = await r.get(_vid_key(mobile, purpose)) if r else None
        if not vid:
            return False

        async def _do(tok: str) -> httpx.Response:
            return await get_http_client().get(
                f"{MC_BASE_URL}/verification/v3/validateOtp",
                params={"verificationId": vid, "code": code, "flowType": MC_FLOW_TYPE},
                headers={"authToken": tok},
                timeout=10,
            )

        try:
            token = await VerifyNowService._get_token()
            resp = await _do(token)
            if resp.status_code == 401:
                resp = await _do(await VerifyNowService._get_token(force_refresh=True))
            data = resp.json()
        except (httpx.HTTPError, ValueError, VerifyNowError) as e:
            logger.error("VerifyNow validate error", extra={"extra_data": {"error": str(e)}})
            return False

        # On failure (e.g. responseCode 702 WRONG_OTP_PROVIDED) `data` is null,
        # so coalesce to {} before reading verificationStatus.
        ok = (
            str(data.get("responseCode")) == "200"
            and (data.get("data") or {}).get("verificationStatus") == "VERIFICATION_COMPLETED"
        )
        if ok and r:
            await r.delete(_vid_key(mobile, purpose))  # one-time use
        return ok
