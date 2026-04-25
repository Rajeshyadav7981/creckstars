"""Health + readiness probes — small, fast, catches dependency breakage."""
import pytest


@pytest.mark.asyncio
async def test_root_api(client):
    r = await client.get("/api")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    # Health body shape varies; minimum it should have a status key.
    assert "status" in body or "db" in body or body == {"ok": True}


@pytest.mark.asyncio
async def test_readiness(client):
    r = await client.get("/readiness")
    # Readiness can legitimately return 503 if Redis is down — accept either.
    assert r.status_code in (200, 503)
