from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from src.database.postgres.db import get_async_db
from src.database.postgres.schemas.push_token_schema import PushTokenSchema, MatchSubscriptionSchema
from src.utils.security import get_current_user
from src.app.api.routers.models.notification_model import PushTokenRequest, RemovePushTokenRequest

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.post("/push-token")
async def register_push_token(
    data: PushTokenRequest,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_async_db),
):
    """Register an Expo push token for the current user."""
    token = data.token
    device_type = data.device_type or "unknown"

    # Upsert: update if exists, insert if new
    existing = await session.execute(
        select(PushTokenSchema).where(
            PushTokenSchema.user_id == user.id,
            PushTokenSchema.expo_push_token == token,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.device_type = device_type
    else:
        session.add(PushTokenSchema(
            user_id=user.id,
            expo_push_token=token,
            device_type=device_type,
        ))
    await session.commit()
    return {"status": "registered"}


@router.delete("/push-token")
async def remove_push_token(
    data: RemovePushTokenRequest,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_async_db),
):
    """Remove a push token (e.g., on logout)."""
    token = data.token
    if token:
        await session.execute(
            delete(PushTokenSchema).where(
                PushTokenSchema.user_id == user.id,
                PushTokenSchema.expo_push_token == token,
            )
        )
        await session.commit()
    return {"status": "removed"}


@router.post("/subscribe/{match_id}")
async def subscribe_to_match(
    match_id: int,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_async_db),
):
    """Subscribe to push notifications for a match."""
    existing = await session.execute(
        select(MatchSubscriptionSchema).where(
            MatchSubscriptionSchema.user_id == user.id,
            MatchSubscriptionSchema.match_id == match_id,
        )
    )
    if not existing.scalar_one_or_none():
        session.add(MatchSubscriptionSchema(
            user_id=user.id,
            match_id=match_id,
        ))
        await session.commit()
    return {"status": "subscribed"}


@router.delete("/subscribe/{match_id}")
async def unsubscribe_from_match(
    match_id: int,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_async_db),
):
    """Unsubscribe from a match's notifications."""
    await session.execute(
        delete(MatchSubscriptionSchema).where(
            MatchSubscriptionSchema.user_id == user.id,
            MatchSubscriptionSchema.match_id == match_id,
        )
    )
    await session.commit()
    return {"status": "unsubscribed"}


@router.get("/subscriptions")
async def get_subscriptions(
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_async_db),
):
    """Get all match subscriptions for current user."""
    result = await session.execute(
        select(MatchSubscriptionSchema.match_id).where(
            MatchSubscriptionSchema.user_id == user.id,
        )
    )
    return [r[0] for r in result.all()]
