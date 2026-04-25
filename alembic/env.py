"""Alembic env — wired to the app's async DB URL + ORM metadata.

Importing every schema module forces SQLAlchemy to register each table on
Base.metadata, which is what `alembic revision --autogenerate` compares against
the live database.
"""
import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.app.api.config import ASYNC_DATABASE_URL  # noqa: E402
from src.database.postgres.db import Base  # noqa: E402

# Side-effect imports: each schema registers its table on Base.metadata.
import src.database.postgres.schemas.user_schema  # noqa: F401, E402
import src.database.postgres.schemas.player_schema  # noqa: F401, E402
import src.database.postgres.schemas.team_schema  # noqa: F401, E402
import src.database.postgres.schemas.team_player_schema  # noqa: F401, E402
import src.database.postgres.schemas.venue_schema  # noqa: F401, E402
import src.database.postgres.schemas.tournament_schema  # noqa: F401, E402
import src.database.postgres.schemas.tournament_stage_schema  # noqa: F401, E402
import src.database.postgres.schemas.tournament_team_schema  # noqa: F401, E402
import src.database.postgres.schemas.tournament_group_schema  # noqa: F401, E402
import src.database.postgres.schemas.tournament_group_team_schema  # noqa: F401, E402
import src.database.postgres.schemas.match_schema  # noqa: F401, E402
import src.database.postgres.schemas.match_squad_schema  # noqa: F401, E402
import src.database.postgres.schemas.match_event_schema  # noqa: F401, E402
import src.database.postgres.schemas.innings_schema  # noqa: F401, E402
import src.database.postgres.schemas.delivery_schema  # noqa: F401, E402
import src.database.postgres.schemas.over_schema  # noqa: F401, E402
import src.database.postgres.schemas.batting_scorecard_schema  # noqa: F401, E402
import src.database.postgres.schemas.bowling_scorecard_schema  # noqa: F401, E402
import src.database.postgres.schemas.fall_of_wicket_schema  # noqa: F401, E402
import src.database.postgres.schemas.partnership_schema  # noqa: F401, E402
import src.database.postgres.schemas.otp_schema  # noqa: F401, E402
import src.database.postgres.schemas.post_schema  # noqa: F401, E402
import src.database.postgres.schemas.push_token_schema  # noqa: F401, E402

config = context.config
config.set_main_option("sqlalchemy.url", ASYNC_DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
