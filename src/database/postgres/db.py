import os
from threading import Lock
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from src.app.api.config import DATABASE_URL

Base = declarative_base()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


class Database:
    """Singleton database connection manager."""

    _instances = {}
    _lock = Lock()

    def __new__(cls, db_url: str = None):
        with cls._lock:
            if db_url not in cls._instances:
                instance = super().__new__(cls)
                instance._initialize(db_url)
                cls._instances[db_url] = instance
        return cls._instances[db_url]

    def _initialize(self, db_url: str):
        async_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
        # Detect if SSL is needed (Neon, Supabase, etc.)
        use_ssl = os.getenv("DB_SSLMODE", "") == "require" or "neon.tech" in db_url or "supabase" in db_url
        conn_args = {
            "server_settings": {
                "statement_timeout": _int_env("DB_STATEMENT_TIMEOUT_MS", 30000).__str__(),
                "idle_in_transaction_session_timeout": _int_env("DB_IDLE_TXN_TIMEOUT_MS", 60000).__str__(),
            },
        }
        if use_ssl:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            conn_args["ssl"] = ctx
        # Pool sized for the rate-limit ceiling (120 deliveries/min × N workers).
        # Defaults assume 2 uvicorn workers on a small VM; tune via env vars.
        self.async_engine = create_async_engine(
            async_url,
            pool_size=_int_env("DB_POOL_SIZE", 20),
            max_overflow=_int_env("DB_MAX_OVERFLOW", 10),
            pool_recycle=_int_env("DB_POOL_RECYCLE_S", 1800),
            pool_timeout=_int_env("DB_POOL_TIMEOUT_S", 5),
            pool_pre_ping=True,
            echo=False,
            future=True,
            connect_args=conn_args,
        )
        self.AsyncSessionLocal = sessionmaker(
            self.async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )


db = Database(DATABASE_URL)


async def get_async_db():
    """FastAPI dependency. Rolls back any uncommitted work on exception so partial-write bugs don't corrupt the session state."""
    async with db.AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
