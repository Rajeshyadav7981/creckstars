"""Reproduces the original 500 by triggering the existing-batting-card branch.

Path:
  1. Innings starts → cards exist for striker (#1) + non-striker (#2)
  2. Score a wicket → new batsman (#3) comes in, gets a card
  3. Retire #3 → he now has a card with how_out='retired hurt', is_out=False
  4. Retire the surviving batsman (#1 or #2), bring in #4 → fresh card path (no bug here)
  5. NOW try to bring #3 back as a future replacement → his card exists, is_out=False
     OLD code: existing.scalar_one_or_none() ... existing.scalar_one_or_none().is_out  → 500
     NEW code: cleanly evaluates is_out, allows retired-hurt batter to return
"""
import asyncio, sys
import asyncpg, httpx, redis.asyncio as redis
import tournament_regression as tr
import live_match_regression as lr
from src.utils.security import hash_password

MOBILE = "7981212220"
PASSWORD = "Rajesh@123"


async def main():
    tr.R = redis.from_url("redis://localhost:6379", decode_responses=True)
    tr.PG = await asyncpg.connect(host="localhost", port=54322, user="demo", password="demo", database="demo")
    lr.PG = tr.PG
    await tr.PG.execute("UPDATE users SET password=$1 WHERE mobile=$2", hash_password(PASSWORD), MOBILE)

    async with httpx.AsyncClient(base_url=tr.BASE, timeout=20) as c:
        r = await c.post("/api/auth/login", json={"mobile": MOBILE, "password": PASSWORD})
        token = r.json()["access_token"]
        tr.ADMIN_ID = r.json()["user"]["id"]
        lr.ADMIN_ID = tr.ADMIN_ID

    h = {"Authorization": f"Bearer {token}"}
    sfx = tr.rand_str(4)

    passed = failed = 0
    def check(label, cond, got=""):
        nonlocal passed, failed
        if cond: passed += 1; print(f"   ✓ {label}")
        else: failed += 1; print(f"   ✗ {label}  ← {got}")

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        ta = await lr.db_create_team(f"RHC-A-{sfx}")
        tb = await lr.db_create_team(f"RHC-B-{sfx}")
        pa = await lr.db_create_players_for_team(ta, lr.INDIA_NAMES)
        pb = await lr.db_create_players_for_team(tb, lr.PAK_NAMES)
        sa = [p["id"] for p in pa]
        sb = [p["id"] for p in pb]

        t = await tr.mk_tournament(c, f"RHC-{sfx}", type_="knockout")
        tid = t["id"]
        for x in (ta, tb):
            await tr.add_team(c, tid, x["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "final"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "F", "team_ids": [ta["id"], tb["id"]]}])
        await tr.generate_matches(c, tid, sid)
        ms = await tr.matches_for_stage(c, tid, sid)
        mid = ms[0]["id"]
        r = await c.patch(f"/api/matches/{mid}", json={"overs": 4}); r.raise_for_status()

        await lr.post_toss(c, mid, ta["id"], "bat")
        await lr.set_squad(c, mid, ta["id"], sa)
        await lr.set_squad(c, mid, tb["id"], sb)
        await lr.start_innings(c, mid,
            batting_team_id=ta["id"],
            striker_id=sa[0], non_striker_id=sa[1], bowler_id=sb[8])

        print(f"\n[setup] mid={mid}, batting squad first 5 = {sa[:5]}")
        print(f"[setup] start: striker={sa[0]} non={sa[1]}\n")

        # Score one ball (no wicket) so positions are stable
        await lr.score(c, mid, batsman_runs=1)

        # 1st retire — striker sa[0] retires, sa[2] comes in (FRESH path)
        r1 = await c.post(f"/api/matches/{mid}/retired-hurt", json={
            "retired_player_id": sa[0], "new_batsman_id": sa[2],
        })
        check("1st retire (fresh batsman path) → 200", r1.status_code == 200, r1.text[:120])

        # 2nd retire — sa[2] himself retires hurt; replacement sa[3] (FRESH path)
        r2 = await c.post(f"/api/matches/{mid}/retired-hurt", json={
            "retired_player_id": sa[2], "new_batsman_id": sa[3],
        })
        check("2nd retire (chains, fresh batsman) → 200", r2.status_code == 200, r2.text[:120])

        # 3rd retire — bring sa[0] BACK as replacement. His card exists with
        # how_out='retired hurt' and is_out=False. OLD code would have 500'd here
        # by calling scalar_one_or_none() twice. NEW code resolves cleanly.
        retire_target = sa[3]
        bring_back = sa[0]
        r3 = await c.post(f"/api/matches/{mid}/retired-hurt", json={
            "retired_player_id": retire_target, "new_batsman_id": bring_back,
        })
        check("3rd retire (replacement HAS existing retired-hurt card) → 200 (not 500)",
              r3.status_code == 200, f"{r3.status_code}: {r3.text[:200]}")

        # Now try bringing back a player who was OUT (dismiss them first via override).
        # We need an actual dismissal first. Score a wicket-out to trigger.
        # Easiest: score a delivery with a wicket on the current striker.
        ls = (await c.get(f"/api/matches/{mid}/live-state")).json()
        cur_striker = ls.get("striker", {}).get("player_id")
        cur_non_striker = ls.get("non_striker", {}).get("player_id")
        # Pick a brand-new replacement for the bowled-out spot
        fresh = next((pid for pid in sa if pid not in (cur_striker, cur_non_striker, sa[0], sa[2], sa[3])), None)
        if fresh:
            await lr.score(c, mid,
                batsman_runs=0, is_wicket=True, wicket_type="bowled",
                dismissed_player_id=cur_striker, new_batsman_id=fresh)
            r4 = await c.post(f"/api/matches/{mid}/retired-hurt", json={
                "retired_player_id": fresh, "new_batsman_id": cur_striker,
            })
            check("4th retire: try bringing BACK an actually-dismissed player → rejected (400, not 500)",
                  r4.status_code == 400,
                  f"{r4.status_code}: {r4.text[:200]}")

    await tr.R.aclose()
    await tr.PG.close()
    print(f"\n=== {passed}/{passed + failed} chained retired-hurt assertions passed ===")
    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
