"""End-to-end auth golden path: register → use token → verify /me.
Cleans up the test user at the end so re-runs don't collide on mobile uniqueness."""
import random
import pytest
from sqlalchemy import text


def _random_mobile() -> str:
    # 10-digit Indian mobile space, starts with 9 to satisfy common validators.
    # Random enough to not collide across quick re-runs.
    return "9" + "".join(str(random.randint(0, 9)) for _ in range(9))


async def _delete_user(mobile: str):
    """Teardown — remove test user + any auto-created player."""
    from src.database.postgres.db import db
    async with db.AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM players WHERE user_id IN "
                 "(SELECT id FROM users WHERE mobile = :m)"),
            {"m": mobile},
        )
        await session.execute(text("DELETE FROM users WHERE mobile = :m"), {"m": mobile})
        await session.commit()


@pytest.mark.asyncio
async def test_register_then_me(client):
    mobile = _random_mobile()
    try:
        r = await client.post("/api/auth/register", json={
            "first_name": "Test",
            "last_name": "Pytest",
            "mobile": mobile,
            "email": None,
            "password": "TestPass123!",
        })
        assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
        body = r.json()
        assert "access_token" in body
        assert body["user"]["mobile"] == mobile

        token = body["access_token"]
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json()["mobile"] == mobile
    finally:
        await _delete_user(mobile)


@pytest.mark.asyncio
async def test_register_auto_creates_player(client):
    """Regression guard for the session's auto-create-on-register fix — every
    newly registered user must have a players row so PlayerProfile is reachable."""
    from src.database.postgres.db import db
    mobile = _random_mobile()
    try:
        r = await client.post("/api/auth/register", json={
            "first_name": "Auto",
            "last_name": "Player",
            "mobile": mobile,
            "email": None,
            "password": "TestPass123!",
        })
        assert r.status_code in (200, 201)
        user_id = r.json()["user"]["id"]

        async with db.AsyncSessionLocal() as session:
            q = await session.execute(
                text("SELECT id FROM players WHERE user_id = :uid"),
                {"uid": user_id},
            )
            player_id = q.scalar_one_or_none()
        assert player_id is not None, "register did not auto-create a player row"
    finally:
        await _delete_user(mobile)


@pytest.mark.asyncio
async def test_duplicate_mobile_rejected(client):
    mobile = _random_mobile()
    try:
        payload = {
            "first_name": "First",
            "last_name": "User",
            "mobile": mobile,
            "email": None,
            "password": "TestPass123!",
        }
        r1 = await client.post("/api/auth/register", json=payload)
        assert r1.status_code in (200, 201)

        payload["first_name"] = "Second"
        r2 = await client.post("/api/auth/register", json=payload)
        assert r2.status_code == 400
        assert "mobile" in r2.text.lower()
    finally:
        await _delete_user(mobile)


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    mobile = _random_mobile()
    try:
        await client.post("/api/auth/register", json={
            "first_name": "Login",
            "last_name": "Test",
            "mobile": mobile,
            "email": None,
            "password": "CorrectPass123!",
        })
        r = await client.post("/api/auth/login", json={
            "mobile": mobile,
            "password": "WrongPass999!",
        })
        assert r.status_code in (400, 401)
    finally:
        await _delete_user(mobile)
