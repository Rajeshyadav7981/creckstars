"""Telemetry Router — receives error reports and analytics events from the frontend."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.postgres.db import get_async_db
from src.utils.security import get_current_user
from src.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Telemetry"])


class ErrorEntry(BaseModel):
    type: str = "error"
    name: str | None = None
    message: str | None = None
    stack: str | None = None
    context: dict | None = None
    data: dict | None = None
    platform: str | None = None
    version: str | None = None
    timestamp: str | None = None


class ErrorBatchRequest(BaseModel):
    errors: list[ErrorEntry]


@router.post("/errors/batch")
async def report_errors(
    req: ErrorBatchRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Receive error + analytics events from frontend. Batch insert."""
    if not req.errors:
        return {"status": "ok", "count": 0}

    for entry in req.errors[:50]:
        if entry.type == "error":
            logger.error(f"[CLIENT] {entry.message}", extra={"extra_data": {
                "stack": entry.stack, "context": entry.context,
                "platform": entry.platform, "version": entry.version,
                "user_id": user.id,
            }})
        elif entry.type == "event":
            logger.info(f"[ANALYTICS] {entry.name}", extra={"extra_data": {
                "data": entry.data, "platform": entry.platform,
                "version": entry.version, "user_id": user.id,
            }})

    try:
        for entry in req.errors[:50]:
            await session.execute(text("""
                INSERT INTO app_events (user_id, event_type, event_name, message, context, platform, app_version, created_at)
                VALUES (:uid, :type, :name, :msg, :ctx::jsonb, :platform, :version, NOW())
            """), {
                "uid": user.id,
                "type": entry.type,
                "name": entry.name or entry.message or "",
                "msg": entry.message or entry.name or "",
                "ctx": str(entry.context or entry.data or {}),
                "platform": entry.platform,
                "version": entry.version,
            })
        await session.commit()
    except Exception as e:
        logger.warning(f"Failed to store telemetry: {e}")
        # Don't fail the request — telemetry is best-effort

    return {"status": "ok", "count": len(req.errors)}


@router.get("/analytics/summary")
async def analytics_summary(
    session: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Simple analytics dashboard — event counts for the last 7 days."""
    result = await session.execute(text("""
        SELECT event_name, COUNT(*) as count
        FROM app_events
        WHERE event_type = 'event' AND created_at > NOW() - INTERVAL '7 days'
        GROUP BY event_name
        ORDER BY count DESC
        LIMIT 20
    """))
    events = [{"name": r[0], "count": r[1]} for r in result.all()]

    errors = await session.execute(text("""
        SELECT COUNT(*) FROM app_events
        WHERE event_type = 'error' AND created_at > NOW() - INTERVAL '7 days'
    """))
    error_count = errors.scalar() or 0

    return {"events": events, "errors_7d": error_count}
