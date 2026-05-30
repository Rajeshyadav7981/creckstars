"""End-to-end regression for the declare-innings endpoint."""
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
        ta = await lr.db_create_team(f"DEC-A-{sfx}")
        tb = await lr.db_create_team(f"DEC-B-{sfx}")
        pa = await lr.db_create_players_for_team(ta, lr.INDIA_NAMES)
        pb = await lr.db_create_players_for_team(tb, lr.PAK_NAMES)
        sa = [p["id"] for p in pa]
        sb = [p["id"] for p in pb]

        t = await tr.mk_tournament(c, f"DEC-{sfx}", type_="knockout")
        tid = t["id"]
        for x in (ta, tb):
            await tr.add_team(c, tid, x["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "final"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "F", "team_ids": [ta["id"], tb["id"]]}])
        await tr.generate_matches(c, tid, sid)
        ms = await tr.matches_for_stage(c, tid, sid)
        mid = ms[0]["id"]
        r = await c.patch(f"/api/matches/{mid}", json={"overs": 5}); r.raise_for_status()

        await lr.post_toss(c, mid, ta["id"], "bat")
        await lr.set_squad(c, mid, ta["id"], sa)
        await lr.set_squad(c, mid, tb["id"], sb)
        await lr.start_innings(c, mid,
            batting_team_id=ta["id"],
            striker_id=sa[0], non_striker_id=sa[1], bowler_id=sb[8])

        # Score some balls — partial innings, far from all-out / all-overs
        await lr.score(c, mid, batsman_runs=4, is_boundary=True)
        await lr.score(c, mid, batsman_runs=1)
        await lr.score(c, mid, batsman_runs=2)
        await lr.score(c, mid, batsman_runs=6, is_six=True)

        # Declare while plenty of overs and wickets remain
        r = await c.post(f"/api/matches/{mid}/declare-innings")
        check("declare-innings → 200", r.status_code == 200, r.text[:120])
        body = r.json()
        check("response.declared == True", body.get("declared") is True, str(body))

        # DB truth: status=completed + declared=true
        row = await tr.PG.fetchrow(
            "SELECT status, declared, total_runs FROM innings WHERE match_id=$1 AND innings_number=1", mid)
        check("innings.status == 'completed' in DB", row["status"] == "completed", str(row["status"]))
        check("innings.declared == true in DB", row["declared"] is True, str(row["declared"]))
        check("total_runs preserved (13)", row["total_runs"] == 13, str(row["total_runs"]))

        # Scorecard endpoint surfaces declared
        sc = (await c.get(f"/api/matches/{mid}/scorecard")).json()
        inn1 = next((i for i in sc.get("innings", []) if i.get("innings_number") == 1), None)
        check("scorecard surfaces declared=true", inn1 and inn1.get("declared") is True,
              str(inn1 and inn1.get("declared")))

        # Idempotency / re-declare protection: second call must fail
        r2 = await c.post(f"/api/matches/{mid}/declare-innings")
        check("re-declare completed innings → not 200", r2.status_code != 200, str(r2.status_code))

        # Now start innings 2 and verify chase target is set correctly
        await lr.start_innings(c, mid,
            batting_team_id=tb["id"],
            striker_id=sb[0], non_striker_id=sb[1], bowler_id=sa[8])

        inn2_row = await tr.PG.fetchrow(
            "SELECT target, status, declared FROM innings WHERE match_id=$1 AND innings_number=2", mid)
        check("innings 2 target == 14 (declared total + 1)",
              inn2_row["target"] == 14, str(inn2_row["target"]))
        check("innings 2 is in_progress", inn2_row["status"] == "in_progress", str(inn2_row["status"]))
        check("innings 2 declared starts as False",
              inn2_row["declared"] is False, str(inn2_row["declared"]))

        # Bowl-out the chasing side OR declare innings 2 to see end-match flow
        # Easier: declare innings 2 short (they concede, conceptually)
        await lr.score(c, mid, batsman_runs=1)
        r3 = await c.post(f"/api/matches/{mid}/declare-innings")
        check("declare innings 2 → 200", r3.status_code == 200, r3.text[:120])

        await lr.end_match(c, mid)
        det = (await c.get(f"/api/matches/{mid}")).json()
        check("match → completed", det.get("status") == "completed", str(det.get("status")))

        # The earlier-declared side should win since they had more runs (13 vs 1)
        check("winner is the declarer (team A)", det.get("winner_id") == ta["id"],
              f"winner_id={det.get('winner_id')} vs team_a_id={ta['id']}")

    await tr.R.aclose()
    await tr.PG.close()
    print(f"\n=== {passed}/{passed + failed} declare-innings assertions passed ===")
    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
