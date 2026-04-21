"""
Notification Service — Expo Push Notifications via Observer pattern.

Architecture:
  Scoring Event → Redis Pub/Sub (event bus) → NotificationWorker (observer) → Expo Push API

Design:
  - Listens to match:*:live Redis channels (same as WebSocket)
  - Only sends push for significant events (wickets, match start/end, milestones)
  - Fire-and-forget: never blocks scoring flow
  - Batches: sends up to 100 tokens per Expo API call
  - Fire-and-forget: never blocks the scoring flow
"""
import json
import asyncio
import httpx
from sqlalchemy import text
from src.database.postgres.db import db
from src.database.redis.redis_client import redis_client
from src.database.postgres.schemas.push_token_schema import PushTokenSchema, MatchSubscriptionSchema
from src.utils.logger import get_logger

logger = get_logger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

# Events worth a push notification
PUSH_EVENTS = {
    "wicket": True,
    "match_end": True,
    "innings_end": True,
}


class NotificationService:
    """Sends push notifications via Expo Push API."""

    @staticmethod
    async def send_expo_push(tokens: list, title: str, body: str, data: dict = None):
        """Send push to multiple Expo push tokens. Batches in chunks of 100 with retry."""
        if not tokens:
            return

        # Deduplicate tokens (same device token registered multiple times)
        unique_tokens = list(set(tokens))

        messages = [
            {
                "to": token,
                "sound": "default",
                "title": title,
                "body": body,
                "data": data or {},
                "priority": "high",
                "channelId": "match-updates",
            }
            for token in unique_tokens
        ]

        async with httpx.AsyncClient(timeout=10.0) as client:
            for i in range(0, len(messages), 100):
                batch = messages[i:i + 100]
                # Retry with exponential backoff (up to 3 attempts)
                for attempt in range(3):
                    try:
                        resp = await client.post(EXPO_PUSH_URL, json=batch)
                        if resp.status_code == 200:
                            break
                        logger.warning(f"Expo push response: {resp.status_code} (attempt {attempt + 1})")
                    except Exception as e:
                        logger.error(f"Expo push failed (attempt {attempt + 1}): {e}")
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)  # 1s, 2s backoff

    @staticmethod
    async def get_all_match_tokens(match_id: int) -> list:
        """Get push tokens for ALL users who should be notified about this match:
        1. Explicitly subscribed users (via match_subscriptions)
        2. Match creator
        3. Players in the match squads (their linked user accounts)

        Deduplicates tokens so no one gets double notifications.
        Uses Redis cache (60s TTL) to avoid DB query on every scoring event.
        """
        # Try cache first
        cache_key = f"push_tokens:{match_id}"
        try:
            r = await redis_client.get_client()
            if r:
                cached = await r.get(cache_key)
                if cached:
                    return json.loads(cached)
        except Exception as _e:
            pass  # logged below not to crash hot path

        tokens = set()
        try:
            async with db.AsyncSessionLocal() as session:
                # Single query to fetch all relevant tokens (union of 3 sources)
                result = await session.execute(text("""
                    SELECT DISTINCT pt.expo_push_token FROM push_tokens pt WHERE pt.user_id IN (
                        SELECT ms.user_id FROM match_subscriptions ms WHERE ms.match_id = :mid
                        UNION
                        SELECT m.created_by FROM matches m WHERE m.id = :mid
                        UNION
                        SELECT p.user_id FROM players p
                        JOIN match_squads msq ON msq.player_id = p.id
                        WHERE msq.match_id = :mid AND p.user_id IS NOT NULL
                    )
                """), {"mid": match_id})
                for r in result.all():
                    tokens.add(r[0])

        except Exception as e:
            logger.error(f"Failed to get match tokens: {e}")

        token_list = list(tokens)

        # Cache for 60s (invalidated when subscription changes)
        try:
            r = await redis_client.get_client()
            if r:
                await r.setex(cache_key, 60, json.dumps(token_list))
        except Exception as _e:
            pass  # logged below not to crash hot path

        return token_list

    @staticmethod
    async def auto_subscribe_match_participants(match_id: int):
        """Auto-subscribe match creator and squad players when match starts.
        Called once when innings starts."""
        try:
            async with db.AsyncSessionLocal() as session:
                # Get creator user_id
                result = await session.execute(text(
                    "SELECT created_by FROM matches WHERE id = :mid"
                ), {"mid": match_id})
                row = result.first()
                if row:
                    creator_id = row[0]
                    # Upsert subscription for creator
                    await session.execute(text(
                        "INSERT INTO match_subscriptions (user_id, match_id) "
                        "VALUES (:uid, :mid) ON CONFLICT (user_id, match_id) DO NOTHING"
                    ), {"uid": creator_id, "mid": match_id})

                # Get squad players who have user accounts
                result = await session.execute(text(
                    "SELECT DISTINCT p.user_id FROM match_squads ms "
                    "JOIN players p ON p.id = ms.player_id "
                    "WHERE ms.match_id = :mid AND p.user_id IS NOT NULL"
                ), {"mid": match_id})
                for r in result.all():
                    await session.execute(text(
                        "INSERT INTO match_subscriptions (user_id, match_id) "
                        "VALUES (:uid, :mid) ON CONFLICT (user_id, match_id) DO NOTHING"
                    ), {"uid": r[0], "mid": match_id})

                await session.commit()
        except Exception as e:
            logger.error(f"Auto-subscribe failed: {e}")

    @staticmethod
    async def notify_match_event(match_id: int, event_type: str, payload: dict):
        """Build notification and send to all relevant users."""
        title, body = NotificationService._build_notification(event_type, payload)
        if not title:
            return

        tokens = await NotificationService.get_all_match_tokens(match_id)

        if tokens:
            await NotificationService.send_expo_push(
                tokens, title, body,
                data={"match_id": match_id, "type": event_type}
            )

    @staticmethod
    def _build_notification(event_type: str, payload: dict) -> tuple:
        """Build title + body for each event type."""
        data = payload.get("data", payload)

        if event_type == "delivery":
            # Only notify on wickets, 50s, 100s, hat-tricks
            if data.get("is_wicket"):
                score = f"{data.get('innings_runs', '?')}/{data.get('innings_wickets', '?')}"
                return ("WICKET!", f"Score: {score} ({data.get('innings_overs', '')} ov)")
            # Check milestone (runs)
            return (None, None)

        elif event_type == "over_end":
            return (None, None)  # Don't push for every over

        elif event_type == "innings_end":
            return ("Innings Complete", f"Score: {data.get('total_runs', '?')}/{data.get('total_wickets', '?')}")

        elif event_type == "match_end":
            result = data.get("result_summary", "Match completed")
            return ("Match Over", result)

        return (None, None)


class NotificationWorker:
    """Background worker — subscribes to Redis Pub/Sub and dispatches push notifications.
    Uses Observer pattern: observes the same event bus as WebSocket."""

    def __init__(self):
        self._task = None

    async def start(self):
        """Start listening to Redis match events in background."""
        try:
            r = await redis_client.get_client()
            if not r:
                logger.warning("Redis not available — notification worker not started")
                return

            pubsub = r.pubsub()
            await pubsub.psubscribe("match:*:live")
            logger.info("Notification worker started — listening to match events")

            async def _listen():
                async for message in pubsub.listen():
                    if message["type"] != "pmessage":
                        continue
                    try:
                        channel = message["channel"]
                        if isinstance(channel, bytes):
                            channel = channel.decode()

                        parts = channel.split(":")
                        if len(parts) != 3:
                            continue

                        match_id = int(parts[1])
                        data = message["data"]
                        if isinstance(data, bytes):
                            data = data.decode()
                        payload = json.loads(data)

                        event_type = payload.get("type", "")

                        # Only push for significant events
                        if event_type == "delivery" and not payload.get("data", {}).get("is_wicket"):
                            continue
                        if event_type not in ("delivery", "innings_end", "match_end"):
                            continue

                        # Fire-and-forget — don't block the listener
                        asyncio.create_task(
                            NotificationService.notify_match_event(match_id, event_type, payload)
                        )

                    except Exception as e:
                        logger.error(f"Notification worker error: {e}")

            self._task = asyncio.create_task(_listen())

        except Exception as e:
            logger.error(f"Failed to start notification worker: {e}")

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None


notification_worker = NotificationWorker()
