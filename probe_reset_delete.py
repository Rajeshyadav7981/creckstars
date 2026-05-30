import asyncio, sys
import asyncpg, httpx, redis.asyncio as redis
import tournament_regression as tr
from src.utils.security import hash_password

MOBILE = "7981212220"
PASSWORD = "Rajesh@123"


async def main():
    tr.R = redis.from_url("redis://localhost:6379", decode_responses=True)
    tr.PG = await asyncpg.connect(host="localhost", port=54322, user="demo", password="demo", database="demo")
    await tr.PG.execute("UPDATE users SET password=$1 WHERE mobile=$2", hash_password(PASSWORD), MOBILE)

    async with httpx.AsyncClient(base_url=tr.BASE, timeout=30) as c:
        r = await c.post("/api/auth/login", json={"mobile": MOBILE, "password": PASSWORD})
        r.raise_for_status()
        token = r.json()["access_token"]
        tr.ADMIN_ID = r.json()["user"]["id"]

    h = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        teams = await tr.db_create_teams(4, "RST")
        t = await tr.mk_tournament(c, f"RST-{tr.rand_str(4)}", type_="knockout")
        tid = t["id"]
        for tm in teams:
            await tr.add_team(c, tid, tm["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "semi_final"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "SF", "team_ids": [tm["id"] for tm in teams]}])
        await tr.generate_matches(c, tid, sid)

        ms = await tr.matches_for_stage(c, tid, sid)
        print(f"[setup] tid={tid} sid={sid} matches={len(ms)}")

        print("\n[A] RESET on an in_progress stage (no matches played yet):")
        r1 = await c.post(f"/api/tournaments/{tid}/stages/{sid}/reset")
        print(f"   POST /reset → {r1.status_code} {r1.text[:200]}")

        ms2 = await tr.matches_for_stage(c, tid, sid)
        print(f"   matches after reset: {len(ms2)} (should be 0 since reset wipes them)")

        print("\n[B] regenerate then partial-play then RESET:")
        await tr.generate_matches(c, tid, sid)
        ms3 = await tr.matches_for_stage(c, tid, sid)
        await tr.complete_match_walkover(c, tid, ms3[0]["id"], ms3[0]["team_a_id"])
        print(f"   completed 1/2 matches")
        r2 = await c.post(f"/api/tournaments/{tid}/stages/{sid}/reset")
        print(f"   POST /reset → {r2.status_code} {r2.text[:200]}")

        print("\n[C] DELETE the stage:")
        r3 = await c.delete(f"/api/tournaments/{tid}/stages/{sid}")
        print(f"   DELETE /stages/{sid} → {r3.status_code} {r3.text[:200]}")

        det = (await c.get(f"/api/tournaments/{tid}")).json()
        print(f"   stages after delete: {[s.get('stage_id') for s in det.get('stages', [])]}")

        print("\n[D] DELETE a stage that has FULLY PLAYED matches with scorecards:")
        stages = await tr.setup_stages(c, tid, [{"name": "final"}])
        sid2 = stages[0]["id"]
        det = (await c.get(f"/api/tournaments/{tid}")).json()
        await tr.setup_groups(c, tid, sid2, [{"name": "F", "team_ids": [teams[0]["id"], teams[1]["id"]]}])
        await tr.generate_matches(c, tid, sid2)
        ms4 = await tr.matches_for_stage(c, tid, sid2)
        await tr.complete_match_walkover(c, tid, ms4[0]["id"], ms4[0]["team_a_id"])
        r4 = await c.delete(f"/api/tournaments/{tid}/stages/{sid2}")
        print(f"   DELETE /stages/{sid2} → {r4.status_code} {r4.text[:200]}")

    await tr.R.aclose()
    await tr.PG.close()


asyncio.run(main())
