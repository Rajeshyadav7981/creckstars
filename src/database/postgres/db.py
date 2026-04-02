from threading import Lock
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from src.app.api.config import DATABASE_URL

Base = declarative_base()


class Database:
    """Singleton database connection manager following nltaggregate pattern."""

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
        # Async engine
        async_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
        # Detect if SSL is needed (Neon, Supabase, etc.)
        import os
        use_ssl = os.getenv("DB_SSLMODE", "") == "require" or "neon.tech" in db_url or "supabase" in db_url
        conn_args = {
            "server_settings": {
                "statement_timeout": "30000",  # 30s max per statement
            },
        }
        if use_ssl:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            conn_args["ssl"] = ctx
        self.async_engine = create_async_engine(
            async_url,
            pool_size=10,
            max_overflow=20,
            pool_recycle=1800,
            pool_timeout=10,
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


# Global instance
db = Database(DATABASE_URL)


async def get_async_db():
    async with db.AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
