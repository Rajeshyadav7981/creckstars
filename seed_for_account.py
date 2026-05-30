"""Seed the regression-style tournaments under a real user account.

Re-uses the scenarios from tournament_regression.py but binds them to a
specific mobile number so the tournaments show up in the app UI for that
login (instead of random throwaway admins).
"""
import asyncio
import sys

import httpx

import tournament_regression as tr
from src.utils.security import hash_password


MOBILE = "7981212220"
PASSWORD = "Rajesh@123"
FIRST_NAME = "Rajesh"
LAST_NAME = "Y"


async def ensure_password_matches():
    """If the existing account's password is unknown, overwrite it with PASSWORD."""
    row = await tr.PG.fetchrow("SELECT id FROM users WHERE mobile = $1", MOBILE)
    if not row:
        return
    new_hash = hash_password(PASSWORD)
    await tr.PG.execute("UPDATE users SET password = $1 WHERE mobile = $2", new_hash, MOBILE)


async def login_or_register() -> tuple[str, int]:
    async with httpx.AsyncClient(base_url=tr.BASE, timeout=20) as c:
        r = await c.post("/api/auth/login", json={"mobile": MOBILE, "password": PASSWORD})
        if r.status_code == 200:
            body = r.json()
            return body["access_token"], body["user"]["id"]
        if r.status_code == 401:
            await ensure_password_matches()
            r = await c.post("/api/auth/login", json={"mobile": MOBILE, "password": PASSWORD})
            if r.status_code == 200:
                body = r.json()
                return body["access_token"], body["user"]["id"]
        if r.status_code in (404, 403):
            await tr.seed_register_flag(MOBILE)
            r = await c.post("/api/auth/register", json={
                "first_name": FIRST_NAME, "last_name": LAST_NAME,
                "mobile": MOBILE, "email": None, "password": PASSWORD,
            })
            if r.status_code in (200, 201):
                body = r.json()
                return body["access_token"], body["user"]["id"]
        raise RuntimeError(f"login/register failed: {r.status_code} {r.text}")


async def main():
    import redis.asyncio as redis
    import asyncpg

    tr.R = redis.from_url("redis://localhost:6379", decode_responses=True)
    tr.PG = await asyncpg.connect(
        host="localhost", port=54322, user="demo", password="demo", database="demo",
    )

    print(f"logging in {MOBILE}…")
    token, uid = await login_or_register()
    tr.ADMIN_ID = uid
    print(f"got token (len={len(token)}), user_id={uid}\n")

    scenarios = [
        tr.scenario_knockout_n(token, 2, "final", "Final", custom_label="Grand Final"),
        tr.scenario_knockout_n(token, 4, "semi_final", "Semi Final"),
        tr.scenario_knockout_n(token, 5, "quarter_final", "Quarter Final"),
        tr.scenario_knockout_n(token, 8, "quarter_final", "Quarter Final"),
        tr.scenario_knockout_n(token, 9, "round_of_16", "Round of 16"),
        tr.scenario_knockout_n(token, 17, "preliminary_round", "Preliminary Round"),
        tr.scenario_knockout_cascade_8(token),
        tr.scenario_league_only_4(token),
        tr.scenario_league_plus_knockout_4(token),
    ]
    results = []
    for coro in scenarios:
        res = await coro
        results.append(res)
        flag = "PASS" if res.ok else "FAIL"
        print(f"[{flag}] {res.name}")
        if not res.ok:
            print(f"       → {res.detail}")

    await tr.R.aclose()
    await tr.PG.close()
    passed = sum(1 for r in results if r.ok)
    print(f"\n=== {passed}/{len(results)} created under {MOBILE} ===")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
