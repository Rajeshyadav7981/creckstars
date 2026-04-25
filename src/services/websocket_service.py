import json
import asyncio
import os
from fastapi import WebSocket
from src.database.redis.redis_client import redis_client
from src.utils.logger import get_logger

logger = get_logger(__name__)


MAX_CONNECTIONS_PER_MATCH = int(os.getenv("WS_MAX_CONNECTIONS_PER_MATCH", "1000"))
# Slow-client eviction: drop a connection after N consecutive send failures.
WS_MAX_CONSECUTIVE_FAILURES = int(os.getenv("WS_MAX_CONSECUTIVE_FAILURES", "3"))
# Per-send timeout (seconds) — anything longer means the client isn't draining.
WS_SEND_TIMEOUT = float(os.getenv("WS_SEND_TIMEOUT_S", "3.0"))


class ConnectionManager:
    """Manages WebSocket connections per match with Redis Pub/Sub for multi-instance support.

    Backpressure handling:
      - ``_failures`` tracks consecutive failed sends per connection.
      - Any send that times out or errors increments the counter and, if the
        threshold is exceeded, the client is force-closed and removed.
      - A successful send resets the counter so transient blips don't evict.
    """

    def __init__(self):
        self.active_connections: dict[int, list[WebSocket]] = {}
        self._failures: dict[int, int] = {}  # id(ws) -> consecutive failure count
        self._subscriber_task = None

    async def connect(self, websocket: WebSocket, match_id: int):
        # Enforce per-match connection cap to prevent memory exhaustion
        current_count = len(self.active_connections.get(match_id, []))
        if current_count >= MAX_CONNECTIONS_PER_MATCH:
            await websocket.close(code=1013, reason="Match connection limit reached")
            return False
        await websocket.accept()
        if match_id not in self.active_connections:
            self.active_connections[match_id] = []
        self.active_connections[match_id].append(websocket)
        self._failures[id(websocket)] = 0
        return True

    def disconnect(self, websocket: WebSocket, match_id: int):
        if match_id in self.active_connections:
            if websocket in self.active_connections[match_id]:
                self.active_connections[match_id].remove(websocket)
            if not self.active_connections[match_id]:
                del self.active_connections[match_id]
        self._failures.pop(id(websocket), None)

    async def broadcast(self, match_id: int, message: dict):
        """Publish to Redis channel + broadcast to local connections."""
        data = json.dumps(message)

        # Publish to Redis for other server instances
        try:
            r = await redis_client.get_client()
            if r:
                await r.publish(f"match:{match_id}:live", data)
        except Exception as e:
            logger.warning(f"Redis publish failed for match {match_id}: {e}")

        # Broadcast to local connections
        await self._broadcast_local(match_id, data)

    async def _broadcast_local(self, match_id: int, data: str):
        """Send to all locally connected WebSocket clients, evicting slow ones."""
        if match_id not in self.active_connections:
            return
        connections = list(self.active_connections[match_id])

        async def _send(ws: WebSocket):
            await asyncio.wait_for(ws.send_text(data), timeout=WS_SEND_TIMEOUT)

        results = await asyncio.gather(
            *(_send(ws) for ws in connections),
            return_exceptions=True,
        )

        to_evict: list[WebSocket] = []
        for ws, result in zip(connections, results):
            wsid = id(ws)
            if isinstance(result, Exception):
                fails = self._failures.get(wsid, 0) + 1
                self._failures[wsid] = fails
                if (
                    isinstance(result, asyncio.TimeoutError)
                    or fails >= WS_MAX_CONSECUTIVE_FAILURES
                ):
                    logger.info(
                        "Evicting slow/dead WebSocket",
                        extra={"extra_data": {
                            "match_id": match_id,
                            "consecutive_failures": fails,
                            "reason": type(result).__name__,
                        }},
                    )
                    to_evict.append(ws)
            else:
                # Successful send — reset the failure counter.
                if self._failures.get(wsid):
                    self._failures[wsid] = 0

        for ws in to_evict:
            try:
                await ws.close(code=1011, reason="slow client")
            except Exception:
                pass
            self.disconnect(ws, match_id)

    async def start_subscriber(self):
        """Subscribe to Redis channels and forward to local WebSocket connections.
        Call this once at app startup."""
        try:
            r = await redis_client.get_client()
            if not r:
                return
            pubsub = r.pubsub()
            await pubsub.psubscribe("match:*:live")

            async def _listen():
                async for message in pubsub.listen():
                    if message["type"] == "pmessage":
                        channel = message["channel"]
                        if isinstance(channel, bytes):
                            channel = channel.decode()
                        # Extract match_id from channel "match:{id}:live"
                        parts = channel.split(":")
                        if len(parts) == 3:
                            try:
                                match_id = int(parts[1])
                                data = message["data"]
                                if isinstance(data, bytes):
                                    data = data.decode()
                                await self._broadcast_local(match_id, data)
                            except (ValueError, Exception) as e:
                                logger.warning(f"Failed to process pub/sub message on channel {channel}: {e}")

            self._subscriber_task = asyncio.create_task(_listen())
        except Exception as e:
            logger.error(f"Failed to start Redis subscriber: {e}")

    def get_connection_count(self, match_id: int) -> int:
        return len(self.active_connections.get(match_id, []))

    def get_total_connections(self) -> int:
        return sum(len(conns) for conns in self.active_connections.values())

    def get_active_match_ids(self) -> list[int]:
        return [mid for mid, conns in self.active_connections.items() if conns]


ws_manager = ConnectionManager()
