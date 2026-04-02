import json
import redis.asyncio as redis
from src.app.api.config import REDIS_URL


class RedisClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._pool = None
        return cls._instance

    async def connect(self):
        if self._pool is None:
            url = REDIS_URL
            kwargs = dict(
                decode_responses=True,
                max_connections=50,
                socket_timeout=3,
                socket_connect_timeout=2,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            # Upstash/cloud Redis uses rediss:// (TLS) — skip cert verification
            if url and url.startswith("rediss://"):
                url = url.replace("rediss://", "redis://")
                kwargs["ssl"] = True
                kwargs["ssl_cert_reqs"] = None
            self._pool = redis.from_url(url, **kwargs)
        return self._pool

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def get_client(self):
        return await self.connect()


redis_client = RedisClient()


async def get_redis():
    return await redis_client.get_client()
