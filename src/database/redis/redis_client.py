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
            url = REDIS_URL or ""
            kwargs = dict(
                decode_responses=True,
                max_connections=50,
                socket_timeout=3,
                socket_connect_timeout=2,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            # Upstash/cloud Redis uses rediss:// (TLS)
            if url.startswith("rediss://"):
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                kwargs["connection_class"] = redis.connection.SSLConnection
                kwargs["ssl_context"] = ctx
                url = url.replace("rediss://", "redis://")
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
