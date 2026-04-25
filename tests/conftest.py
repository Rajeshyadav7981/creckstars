"""Shared pytest fixtures.

Tests run against the same dev database the app uses. Writes are
transaction-scoped where possible; where that's impractical (auth flows
that create real users), tests clean up their own rows.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Project root on path so `from src...` works when pytest is invoked from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Mark every test in this project as asyncio by default; no need to decorate each.
pytest_plugins = ("pytest_asyncio",)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def app():
    from src.app.api.fastapi_app import app as fastapi_app
    return fastapi_app


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
