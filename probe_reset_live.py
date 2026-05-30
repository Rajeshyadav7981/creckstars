import asyncio
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

    async with httpx.AsyncClient(base_url=tr.BASE, timeout=30) as c:
        r = await c.post("/api/auth/login", json={"mobile": MOBILE, "password": PASSWORD})
        token = r.json()["access_token"]
        tr.ADMIN_ID = r.json()["user"]["id"]
        lr.ADMIN_ID = tr.ADMIN_ID

    h = {"Authorization": f"Bearer {token}"}
    sfx = tr.rand_str(4)

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        ta = await lr.db_create_team(f"IndA-{sfx}")
        tb = await lr.db_create_team(f"PakA-{sfx}")
        pa = await lr.db_create_players_for_team(ta, lr.INDIA_NAMES)
        pb = await lr.db_create_players_for_team(tb, lr.PAK_NAMES)
        sa = [p["id"] for p in pa]
        sb = [p["id"] for p in pb]

        t = await tr.mk_tournament(c, f"RSL-{sfx}", type_="knockout")
        tid = t["id"]
        for x in (ta, tb):
            await tr.add_team(c, tid, x["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "final"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "F", "team_ids": [ta["id"], tb["id"]]}])
        await tr.generate_matches(c, tid, sid)
        ms = await tr.matches_for_stage(c, tid, sid)
        mid = ms[0]["id"]
        await (await c.patch(f"/api/matches/{mid}", json={"overs": 2})).aclose() if False else None
        r = await c.patch(f"/api/matches/{mid}", json={"overs": 2}); r.raise_for_status()

        await lr.post_toss(c, mid, ta["id"], "bat")
        await lr.set_squad(c, mid, ta["id"], sa)
        await lr.set_squad(c, mid, tb["id"], sb)
        await lr.start_innings(c, mid, batting_team_id=ta["id"],
                               striker_id=sa[0], non_striker_id=sa[1], bowler_id=sb[8])
        await lr.score(c, mid, batsman_runs=1)
        await lr.score(c, mid, batsman_runs=4, is_boundary=True)
        await lr.score(c, mid, batsman_runs=0, is_wicket=True, wicket_type="caught",
                       dismissed_player_id=sa[0], fielder_id=sb[5], new_batsman_id=sa[2])
        print(f"[setup] tid={tid} sid={sid} mid={mid} — innings 1 live with 3 balls + 1 catch")

        print("\n[A] RESET stage with a LIVE match (innings in progress):")
        r1 = await c.post(f"/api/tournaments/{tid}/stages/{sid}/reset")
        print(f"   POST /reset → {r1.status_code} {r1.text[:300]}")

        print("\n[B] regenerate, replay live, then DELETE stage:")
        await tr.generate_matches(c, tid, sid)
        ms2 = await tr.matches_for_stage(c, tid, sid)
        mid2 = ms2[0]["id"]
        await (await c.patch(f"/api/matches/{mid2}", json={"overs": 2})).aclose() if False else None
        r = await c.patch(f"/api/matches/{mid2}", json={"overs": 2}); r.raise_for_status()
        await lr.post_toss(c, mid2, ta["id"], "bat")
        await lr.set_squad(c, mid2, ta["id"], sa)
        await lr.set_squad(c, mid2, tb["id"], sb)
        await lr.start_innings(c, mid2, batting_team_id=ta["id"],
                               striker_id=sa[0], non_striker_id=sa[1], bowler_id=sb[8])
        await lr.score(c, mid2, batsman_runs=2)
        r2 = await c.delete(f"/api/tournaments/{tid}/stages/{sid}")
        print(f"   DELETE /stages/{sid} → {r2.status_code} {r2.text[:300]}")

    await tr.R.aclose()
    await tr.PG.close()


asyncio.run(main())
