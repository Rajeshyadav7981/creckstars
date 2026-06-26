"""Confirms scorecard endpoint returns how_out='retired hurt' with is_out=false
for a retired batter, so the UI can render the label."""
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
        ta = await lr.db_create_team(f"RHS-A-{sfx}")
        tb = await lr.db_create_team(f"RHS-B-{sfx}")
        pa = await lr.db_create_players_for_team(ta, lr.INDIA_NAMES)
        pb = await lr.db_create_players_for_team(tb, lr.PAK_NAMES)
        sa = [p["id"] for p in pa]
        sb = [p["id"] for p in pb]

        t = await tr.mk_tournament(c, f"RHS-{sfx}", type_="knockout")
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

        # 3 balls, then retire striker (sa[0])
        await lr.score(c, mid, batsman_runs=4, is_boundary=True)
        await lr.score(c, mid, batsman_runs=1)
        await lr.score(c, mid, batsman_runs=2)

        r = await c.post(f"/api/matches/{mid}/retired-hurt", json={
            "retired_player_id": sa[0], "new_batsman_id": sa[2],
        })
        check("retire-hurt → 200", r.status_code == 200, r.text[:120])

        sc = (await c.get(f"/api/matches/{mid}/scorecard")).json()
        innings = sc.get("innings") or []
        check("scorecard has innings array", len(innings) >= 1, str(len(innings)))

        bat_rows = innings[0].get("batting") or []
        retired_row = next((b for b in bat_rows if b.get("player_id") == sa[0]), None)
        check("retired player has a row in batting[]", retired_row is not None, str(retired_row))

        if retired_row:
            check("retired player's how_out == 'retired hurt'",
                  retired_row.get("how_out") == "retired hurt", str(retired_row.get("how_out")))
            check("retired player's is_out == False",
                  retired_row.get("is_out") is False, str(retired_row.get("is_out")))
            check("retired player's runs preserved (5 — scored 4+1 before strike rotated)",
                  retired_row.get("runs") == 5, str(retired_row.get("runs")))

        # Now verify the UI logic on this data: `{b.how_out || 'not out'}`
        # gives the correct label for each case.
        for b in bat_rows:
            displayed = b.get("how_out") or "not out"
            pid = b.get("player_id")
            if pid == sa[0]:
                check(f"UI text for retired batter would render: 'retired hurt'",
                      displayed == "retired hurt", displayed)
            elif b.get("is_out"):
                check(f"UI text for dismissed batter pid={pid}: dismissal shown",
                      displayed not in (None, "", "not out"), displayed)
            else:
                check(f"UI text for active batter pid={pid}: 'not out'",
                      displayed == "not out", displayed)

    await tr.R.aclose()
    await tr.PG.close()
    print(f"\n=== {passed}/{passed + failed} scorecard-retired-hurt assertions passed ===")
    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
