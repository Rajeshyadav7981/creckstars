"""Confirms backend durability — favorites survive across separate logins (= app restarts).

If THIS passes, the client is dropping the API call.
"""
import asyncio, sys
import asyncpg, httpx
from src.utils.security import hash_password

MOBILE = "7981212220"
PASSWORD = "Rajesh@123"
BASE = "http://localhost:7981"


async def fresh_login(client):
    r = await client.post("/api/auth/login", json={"mobile": MOBILE, "password": PASSWORD})
    r.raise_for_status()
    return r.json()["access_token"], r.json()["user"]["id"]


async def main():
    pg = await asyncpg.connect(host="localhost", port=54322, user="demo", password="demo", database="demo")
    await pg.execute("UPDATE users SET password=$1 WHERE mobile=$2", hash_password(PASSWORD), MOBILE)

    async with httpx.AsyncClient(base_url=BASE, timeout=20) as c:
        token1, uid = await fresh_login(c)

    await pg.execute("DELETE FROM user_favorite_matches WHERE user_id=$1", uid)
    await pg.execute("DELETE FROM user_favorite_tournaments WHERE user_id=$1", uid)

    row = await pg.fetchrow(
        "SELECT id, name FROM matches WHERE created_by=$1 ORDER BY id DESC LIMIT 1", uid
    )
    if not row:
        print("FAIL: need at least one match created by this user first")
        sys.exit(1)
    mid = row["id"]

    passed = failed = 0
    def check(label, cond, got=""):
        nonlocal passed, failed
        if cond: passed += 1; print(f"   ✓ {label}")
        else: failed += 1; print(f"   ✗ {label}  ← {got}")

    # Session 1 — login, add favorite, "close" (drop the client)
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as c1:
        token1, _ = await fresh_login(c1)
        h1 = {"Authorization": f"Bearer {token1}"}

        ids_before = (await c1.get("/api/favorites/ids", headers=h1)).json()
        check("session 1: clean slate", ids_before == {"match_ids": [], "tournament_ids": []}, str(ids_before))

        r = await c1.post(f"/api/favorites/matches/{mid}", headers=h1)
        check("session 1: POST favorite → 200", r.status_code == 200, r.text[:80])

        ids_now = (await c1.get("/api/favorites/ids", headers=h1)).json()
        check("session 1: GET ids reflects the add", mid in ids_now.get("match_ids", []), str(ids_now))

    # Session 2 — brand new HTTP client (= app restart, fresh React Query cache)
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as c2:
        token2, _ = await fresh_login(c2)
        h2 = {"Authorization": f"Bearer {token2}"}

        ids_persisted = (await c2.get("/api/favorites/ids", headers=h2)).json()
        check("session 2 (cold start): GET ids still has the favorite",
              mid in ids_persisted.get("match_ids", []), str(ids_persisted))

        matches = (await c2.get("/api/favorites/matches", headers=h2)).json()
        check("session 2: GET /favorites/matches returns the row",
              isinstance(matches, list) and any(m.get("id") == mid for m in matches),
              str([m.get("id") for m in matches]))

        detail = (await c2.get(f"/api/matches/{mid}", headers=h2)).json()
        check("session 2: match detail carries is_favorite=true",
              detail.get("is_favorite") is True, str(detail.get("is_favorite")))

    # Direct DB check — completely bypass HTTP/auth/cache
    row = await pg.fetchrow(
        "SELECT match_id FROM user_favorite_matches WHERE user_id=$1 AND match_id=$2", uid, mid
    )
    check("DB row exists in user_favorite_matches table", row is not None, str(row))

    # Cleanup so we don't leave test data behind
    await pg.execute("DELETE FROM user_favorite_matches WHERE user_id=$1", uid)
    await pg.close()

    print(f"\n=== {passed}/{passed + failed} backend-durability assertions passed ===")
    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
