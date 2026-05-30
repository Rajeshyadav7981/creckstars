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
        r.raise_for_status()
        token = r.json()["access_token"]
        tr.ADMIN_ID = r.json()["user"]["id"]
        lr.ADMIN_ID = tr.ADMIN_ID

    h = {"Authorization": f"Bearer {token}"}
    sfx = tr.rand_str(4)

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        ta = await lr.db_create_team(f"RHurtA-{sfx}")
        tb = await lr.db_create_team(f"RHurtB-{sfx}")
        pa = await lr.db_create_players_for_team(ta, lr.INDIA_NAMES)
        pb = await lr.db_create_players_for_team(tb, lr.PAK_NAMES)
        sa = [p["id"] for p in pa]
        sb = [p["id"] for p in pb]

        t = await tr.mk_tournament(c, f"RH-{sfx}", type_="knockout")
        tid = t["id"]
        for x in (ta, tb):
            await tr.add_team(c, tid, x["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "final"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "F", "team_ids": [ta["id"], tb["id"]]}])
        await tr.generate_matches(c, tid, sid)
        ms = await tr.matches_for_stage(c, tid, sid)
        mid = ms[0]["id"]
        r = await c.patch(f"/api/matches/{mid}", json={"overs": 2}); r.raise_for_status()

        await lr.post_toss(c, mid, ta["id"], "bat")
        await lr.set_squad(c, mid, ta["id"], sa)
        await lr.set_squad(c, mid, tb["id"], sb)
        await lr.start_innings(c, mid, batting_team_id=ta["id"],
                               striker_id=sa[0], non_striker_id=sa[1], bowler_id=sb[8])

        # Score a few balls
        await lr.score(c, mid, batsman_runs=4, is_boundary=True)
        await lr.score(c, mid, batsman_runs=1)
        await lr.score(c, mid, batsman_runs=2)

        row = await tr.PG.fetchrow(
            "SELECT total_runs, total_wickets, current_striker_id, current_non_striker_id "
            "FROM innings WHERE match_id=$1 ORDER BY innings_number DESC LIMIT 1", mid)
        wkts_before = row["total_wickets"] or 0
        runs_before = row["total_runs"] or 0
        striker_before = row["current_striker_id"]
        nonstriker_before = row["current_non_striker_id"]

        # Retire the striker, replacement is squad position 3 (sa[2])
        print(f"[setup] runs={runs_before} wkts={wkts_before} striker={striker_before} non={nonstriker_before}")
        rh = await c.post(f"/api/matches/{mid}/retired-hurt", json={
            "retired_player_id": striker_before,
            "new_batsman_id": sa[2],
        })
        print(f"   POST /retired-hurt → {rh.status_code} {rh.text[:120]}")
        assert rh.status_code == 200, f"expected 200, got {rh.status_code}"

        row2 = await tr.PG.fetchrow(
            "SELECT total_runs, total_wickets, current_striker_id, current_non_striker_id "
            "FROM innings WHERE match_id=$1 ORDER BY innings_number DESC LIMIT 1", mid)
        wkts_after = row2["total_wickets"] or 0
        runs_after = row2["total_runs"] or 0
        striker_after = row2["current_striker_id"]
        nonstriker_after = row2["current_non_striker_id"]
        print(f"[after] runs={runs_after} wkts={wkts_after} striker={striker_after} non={nonstriker_after}")

        passed = 0; failed = 0
        def check(label, cond, got=""):
            nonlocal passed, failed
            if cond:
                passed += 1; print(f"   ✓ {label}")
            else:
                failed += 1; print(f"   ✗ {label}  ← {got}")
        check("total_wickets unchanged (NOT counted as wicket)", wkts_after == wkts_before, f"{wkts_before} → {wkts_after}")
        check("total_runs unchanged (no delivery created)", runs_after == runs_before, f"{runs_before} → {runs_after}")
        check("striker swapped to replacement", striker_after == sa[2], f"expected {sa[2]}, got {striker_after}")
        check("non-striker preserved", nonstriker_after == nonstriker_before, f"{nonstriker_before} → {nonstriker_after}")

        sc_resp = await lr.score(c, mid, batsman_runs=1)
        row3 = await tr.PG.fetchrow(
            "SELECT total_runs FROM innings WHERE match_id=$1 ORDER BY innings_number DESC LIMIT 1", mid)
        check("can score normally after retire (+1 run)", (row3["total_runs"] or 0) == runs_after + 1,
              f"expected {runs_after+1}, got {row3['total_runs']}")

        # Retired player's batting card has how_out='retired hurt', is_out=False
        sc = (await c.get(f"/api/matches/{mid}/scorecard")).json()
        innings = sc.get("innings") or []
        retired_card = None
        for inn in innings:
            for b in inn.get("batting") or []:
                if b.get("player_id") == striker_before:
                    retired_card = b; break
        if retired_card:
            check("retired player's how_out = 'retired hurt'", retired_card.get("how_out") == "retired hurt", str(retired_card.get("how_out")))
            check("retired player's is_out = False", retired_card.get("is_out") is False, str(retired_card.get("is_out")))
        else:
            check("retired player batting card found", False, "missing")

        # Bringing in a player who is already batting must fail
        bad = await c.post(f"/api/matches/{mid}/retired-hurt", json={
            "retired_player_id": nonstriker_after,
            "new_batsman_id": striker_after,
        })
        check("rejects replacement already batting (400)", bad.status_code == 400, f"got {bad.status_code}")

        print(f"\n=== {passed}/{passed + failed} retired-hurt assertions passed ===")
        await tr.R.aclose()
        await tr.PG.close()
        sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
