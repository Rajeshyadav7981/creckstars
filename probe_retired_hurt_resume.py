"""Confirms a retired-hurt batter can come back and resume batting with their
previous score preserved, and that their how_out clears so the UI reads them
as 'not out' while they're at the crease again.

Sequence:
  1. Striker scores 5
  2. Retire striker (sa[0]) → card: runs=5, how_out='retired hurt', is_out=False
  3. Non-striker keeps batting; sa[2] comes in as replacement
  4. Score a couple more balls
  5. Retire sa[2] → bring sa[0] BACK as replacement
  6. sa[0] now back at the crease — their card should still have runs=5
     but how_out should be cleared (None) and is_out=False
  7. Score 3 more runs to sa[0]
  8. Their card should now read runs=5+3=8 with how_out=None (= 'not out' in UI)
  9. If they retire AGAIN later, how_out flips back to 'retired hurt'
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

    async def card_for(c, mid, pid):
        sc = (await c.get(f"/api/matches/{mid}/scorecard")).json()
        rows = (sc.get("innings") or [{}])[0].get("batting") or []
        return next((b for b in rows if b.get("player_id") == pid), None)

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        ta = await lr.db_create_team(f"RHR-A-{sfx}")
        tb = await lr.db_create_team(f"RHR-B-{sfx}")
        pa = await lr.db_create_players_for_team(ta, lr.INDIA_NAMES)
        pb = await lr.db_create_players_for_team(tb, lr.PAK_NAMES)
        sa = [p["id"] for p in pa]
        sb = [p["id"] for p in pb]

        t = await tr.mk_tournament(c, f"RHR-{sfx}", type_="knockout")
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

        # Step 1: striker scores 5 (4 + 1 — but 1 rotates strike to sa[1])
        await lr.score(c, mid, batsman_runs=4, is_boundary=True)
        await lr.score(c, mid, batsman_runs=1)

        cb = await card_for(c, mid, sa[0])
        check("before retire: sa[0] runs == 5", cb and cb.get("runs") == 5, str(cb and cb.get('runs')))

        # Strike rotated, so now sa[1] is striker. Get back to sa[0] as striker by scoring 1.
        await lr.score(c, mid, batsman_runs=1)  # sa[1] rotates strike back to sa[0]

        # Step 2: retire sa[0]
        r = await c.post(f"/api/matches/{mid}/retired-hurt", json={
            "retired_player_id": sa[0], "new_batsman_id": sa[2],
        })
        check("retire sa[0] → 200", r.status_code == 200, r.text[:120])

        cb = await card_for(c, mid, sa[0])
        check("after retire: sa[0].how_out == 'retired hurt'",
              cb and cb.get("how_out") == "retired hurt", str(cb and cb.get('how_out')))
        check("after retire: sa[0].is_out is False",
              cb and cb.get("is_out") is False, str(cb and cb.get('is_out')))
        check("after retire: sa[0] runs preserved (5)",
              cb and cb.get("runs") == 5, str(cb and cb.get('runs')))

        # Step 3: sa[2] now at the crease with sa[1]. Score some balls.
        await lr.score(c, mid, batsman_runs=2)
        await lr.score(c, mid, batsman_runs=1)

        # Step 4: retire sa[2] (or sa[1]) and bring sa[0] BACK
        ls = (await c.get(f"/api/matches/{mid}/live-state")).json()
        cur_striker = ls.get("striker", {}).get("player_id")
        cur_non_striker = ls.get("non_striker", {}).get("player_id")
        retire_target = cur_striker if cur_striker != sa[0] else cur_non_striker

        r = await c.post(f"/api/matches/{mid}/retired-hurt", json={
            "retired_player_id": retire_target, "new_batsman_id": sa[0],
        })
        check("bring sa[0] BACK as replacement → 200", r.status_code == 200, r.text[:120])

        cb = await card_for(c, mid, sa[0])
        check("after return: sa[0].how_out cleared (renders as 'not out')",
              cb and not cb.get("how_out"), f"how_out={cb and cb.get('how_out')!r}")
        check("after return: sa[0].is_out still False",
              cb and cb.get("is_out") is False, str(cb and cb.get('is_out')))
        check("after return: sa[0] runs preserved (still 5)",
              cb and cb.get("runs") == 5, str(cb and cb.get('runs')))

        # Step 5: score 4 more runs for sa[0]. They're now striker again.
        ls = (await c.get(f"/api/matches/{mid}/live-state")).json()
        striker_now = ls.get("striker", {}).get("player_id")
        if striker_now != sa[0]:
            # If sa[0] came back at non-striker, rotate strike via a single
            await lr.score(c, mid, batsman_runs=1)
        await lr.score(c, mid, batsman_runs=4, is_boundary=True)

        cb = await card_for(c, mid, sa[0])
        check("after scoring 4 more: sa[0] runs accumulated (5 + 4 = 9)",
              cb and cb.get("runs") == 9, str(cb and cb.get('runs')))
        check("after scoring more: sa[0] still 'not out'",
              cb and not cb.get("how_out"), str(cb and cb.get('how_out')))

        # Step 6: retire sa[0] AGAIN later — how_out flips back to 'retired hurt'
        ls = (await c.get(f"/api/matches/{mid}/live-state")).json()
        if ls.get("striker", {}).get("player_id") != sa[0]:
            await lr.score(c, mid, batsman_runs=1)  # rotate to sa[0]
        # Find a fresh batsman not currently batting
        ls = (await c.get(f"/api/matches/{mid}/live-state")).json()
        cur_striker = ls.get("striker", {}).get("player_id")
        cur_non_striker = ls.get("non_striker", {}).get("player_id")
        used = {cur_striker, cur_non_striker}
        fresh = next((pid for pid in sa if pid not in used), None)
        if fresh:
            target = sa[0] if sa[0] in (cur_striker, cur_non_striker) else cur_striker
            r = await c.post(f"/api/matches/{mid}/retired-hurt", json={
                "retired_player_id": target, "new_batsman_id": fresh,
            })
            if r.status_code == 200 and target == sa[0]:
                cb = await card_for(c, mid, sa[0])
                check("retire sa[0] a second time: how_out back to 'retired hurt'",
                      cb and cb.get("how_out") == "retired hurt", str(cb and cb.get('how_out')))
                check("second retire: runs still preserved",
                      cb and cb.get("runs") >= 9, str(cb and cb.get('runs')))

    await tr.R.aclose()
    await tr.PG.close()
    print(f"\n=== {passed}/{passed + failed} retire-resume assertions passed ===")
    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
