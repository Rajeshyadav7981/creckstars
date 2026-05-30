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

    async with httpx.AsyncClient(base_url=tr.BASE, timeout=20) as c:
        r = await c.post("/api/auth/login", json={"mobile": MOBILE, "password": PASSWORD})
        r.raise_for_status()
        token = r.json()["access_token"]
        uid = r.json()["user"]["id"]
        tr.ADMIN_ID = uid
    h = {"Authorization": f"Bearer {token}"}

    passed = failed = 0
    def check(label, cond, got=""):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"   ✓ {label}")
        else:
            failed += 1; print(f"   ✗ {label}  ← {got}")

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=30) as c:
        teams = await tr.db_create_teams(2, "FV")
        t = await tr.mk_tournament(c, f"FAV-{tr.rand_str(4)}", type_="knockout")
        tid = t["id"]
        for tm in teams:
            await tr.add_team(c, tid, tm["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "final"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "F", "team_ids": [tm["id"] for tm in teams]}])
        await tr.generate_matches(c, tid, sid)
        ms = await tr.matches_for_stage(c, tid, sid)
        mid = ms[0]["id"]

        await tr.PG.execute("DELETE FROM user_favorite_matches WHERE user_id=$1", uid)
        await tr.PG.execute("DELETE FROM user_favorite_tournaments WHERE user_id=$1", uid)

        print(f"\n[setup] tid={tid}, mid={mid}, uid={uid}\n")

        ids_r = await c.get("/api/favorites/ids")
        check("GET /ids returns 200", ids_r.status_code == 200, str(ids_r.status_code))
        check("clean slate: empty sets",
              ids_r.json() == {"match_ids": [], "tournament_ids": []}, str(ids_r.json()))

        am = await c.post(f"/api/favorites/matches/{mid}")
        check("POST /favorites/matches/{id} → 200", am.status_code == 200, am.text[:80])
        check("response.favorited == True", am.json().get("favorited") is True, str(am.json()))
        check("response.added == True (first time)", am.json().get("added") is True, str(am.json()))

        am2 = await c.post(f"/api/favorites/matches/{mid}")
        check("dup POST is idempotent (200, added=False)",
              am2.status_code == 200 and am2.json().get("added") is False, str(am2.json()))

        at = await c.post(f"/api/favorites/tournaments/{tid}")
        check("POST /favorites/tournaments/{id} → 200", at.status_code == 200, at.text[:80])

        ids_r2 = await c.get("/api/favorites/ids")
        body = ids_r2.json()
        check("ids: match present", mid in body.get("match_ids", []), str(body))
        check("ids: tournament present", tid in body.get("tournament_ids", []), str(body))

        det_m = await c.get(f"/api/matches/{mid}")
        check("GET /matches/{id} carries is_favorite=true",
              det_m.json().get("is_favorite") is True, str(det_m.json().get("is_favorite")))

        det_t = await c.get(f"/api/tournaments/{tid}")
        check("GET /tournaments/{id} carries is_favorite=true",
              det_t.json().get("is_favorite") is True, str(det_t.json().get("is_favorite")))

        lm = await c.get("/api/favorites/matches")
        check("GET /favorites/matches has 1 row",
              isinstance(lm.json(), list) and len(lm.json()) == 1, str(lm.json())[:100])
        if lm.json():
            row = lm.json()[0]
            check("favorite match row has team_a_name", bool(row.get("team_a_name")), str(row.get("team_a_name")))
            check("favorite match row has status", bool(row.get("status")), str(row.get("status")))

        lt = await c.get("/api/favorites/tournaments")
        check("GET /favorites/tournaments has 1 row",
              isinstance(lt.json(), list) and len(lt.json()) == 1, str(lt.json())[:100])
        if lt.json():
            row = lt.json()[0]
            check("favorite tournament row has name", bool(row.get("name")), str(row.get("name")))
            check("favorite tournament row has stages_count", "stages_count" in row, str(row))

        rm = await c.delete(f"/api/favorites/matches/{mid}")
        check("DELETE /favorites/matches → 200", rm.status_code == 200, rm.text)
        check("delete: removed=True", rm.json().get("removed") is True, str(rm.json()))

        rmd = await c.delete(f"/api/favorites/matches/{mid}")
        check("dup DELETE is idempotent (removed=False)",
              rmd.status_code == 200 and rmd.json().get("removed") is False, str(rmd.json()))

        det_m2 = await c.get(f"/api/matches/{mid}")
        check("after remove: is_favorite=false on match GET",
              det_m2.json().get("is_favorite") is False, str(det_m2.json().get("is_favorite")))

        rt = await c.delete(f"/api/favorites/tournaments/{tid}")
        check("DELETE /favorites/tournaments → 200", rt.status_code == 200, rt.text)

        ids_r3 = await c.get("/api/favorites/ids")
        check("clean slate again",
              ids_r3.json() == {"match_ids": [], "tournament_ids": []}, str(ids_r3.json()))

        nf = await c.post("/api/favorites/matches/999999999")
        check("favoriting nonexistent match → 404", nf.status_code == 404, str(nf.status_code))

    async with httpx.AsyncClient(base_url=tr.BASE, timeout=30) as cg:
        gids = await cg.get("/api/favorites/ids")
        check("guest GET /favorites/ids → 401", gids.status_code == 401, str(gids.status_code))

    await tr.R.aclose()
    await tr.PG.close()
    print(f"\n=== {passed}/{passed + failed} favorites assertions passed ===")
    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
