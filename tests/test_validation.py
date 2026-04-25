"""Pydantic validation smoke: bad payloads must be rejected with 4xx, never 5xx.
A 500 here means a validator is leaking exceptions to FastAPI's error handler."""
import pytest


@pytest.mark.asyncio
async def test_login_missing_fields(client):
    r = await client.post("/api/auth/login", json={})
    assert r.status_code in (400, 401, 422)


@pytest.mark.asyncio
async def test_search_users_requires_auth(client):
    # No Authorization header — must be 401, not a 500.
    r = await client.get("/api/users/search", params={"q": "x"})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_lookup_mobile_bad_format(client):
    # Unauthed still gets validated — but the auth check hits first, so 401.
    r = await client.get("/api/users/lookup-by-mobile", params={"mobile": "abc"})
    assert r.status_code in (401, 422)


@pytest.mark.asyncio
async def test_follow_rejects_unauthed(client):
    r = await client.post("/api/users/follow/1")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_match_rejects_unauthed(client):
    r = await client.post("/api/matches", json={})
    assert r.status_code in (401, 403, 422)
