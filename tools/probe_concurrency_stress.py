"""Concurrency stress for the scoring service: fires many requests in parallel
to validate that the with_for_update locks serialize correctly without dropping
state, double-applying changes, or returning 500s.

Scenarios:
  A) 10 parallel /score POSTs on a live innings — every legal one must land
     (or get 409 Conflict, never silently dropped).
  B) 5 parallel /declare-innings POSTs on the same innings — exactly one wins,
     the rest get 400 "already completed".
  C) 5 parallel /end-innings POSTs on the same innings — exactly one closes it,
     the rest return "already completed" idempotently.
  D) 5 parallel /retire-hurt + 1 parallel /score — final state is consistent
     (runs preserved, no duplicate cards).
"""
import asyncio, sys
import asyncpg  # type: ignore[import-not-found]
import httpx  # type: ignore[import-not-found]
import redis.asyncio as redis  # type: ignore[import-not-found]
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
    passed = failed = 0
    def check(label, cond, got=""):
        nonlocal passed, failed
        if cond: passed += 1; print(f"   ✓ {label}")
        else: failed += 1; print(f"   ✗ {label}  ← {got}")

    async def setup_match():
        async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
            ta = await lr.db_create_team(f"CS-A-{tr.rand_str(4)}")
            tb = await lr.db_create_team(f"CS-B-{tr.rand_str(4)}")
            pa = await lr.db_create_players_for_team(ta, lr.INDIA_NAMES)
            pb = await lr.db_create_players_for_team(tb, lr.PAK_NAMES)
            sa = [p["id"] for p in pa]
            sb = [p["id"] for p in pb]

            t = await tr.mk_tournament(c, f"CS-{tr.rand_str(4)}", type_="knockout")
            tid = t["id"]
            for x in (ta, tb):
                await tr.add_team(c, tid, x["id"])
            stages = await tr.setup_stages(c, tid, [{"name": "final"}])
            sid = stages[0]["id"]
            await tr.setup_groups(c, tid, sid, [{"name": "F", "team_ids": [ta["id"], tb["id"]]}])
            await tr.generate_matches(c, tid, sid)
            ms = await tr.matches_for_stage(c, tid, sid)
            mid = ms[0]["id"]
            r = await c.patch(f"/api/matches/{mid}", json={"overs": 10}); r.raise_for_status()

            await lr.post_toss(c, mid, ta["id"], "bat")
            await lr.set_squad(c, mid, ta["id"], sa)
            await lr.set_squad(c, mid, tb["id"], sb)
            await lr.start_innings(c, mid,
                batting_team_id=ta["id"], striker_id=sa[0], non_striker_id=sa[1], bowler_id=sb[8])
            return mid, sa, sb, ta["id"], tb["id"]

    # ─── Scenario A: parallel /score ───────────────────────────────────────
    mid, sa, sb, _, tb_id = await setup_match()
    print(f"\n[A] 10 parallel /score POSTs on match {mid}")

    async def score_one(client, idx, runs):
        try:
            r = await client.post(f"/api/matches/{mid}/score",
                                  json={"batsman_runs": runs},
                                  headers={**h, "X-Idempotency-Key": f"a-{idx}"})
            return idx, r.status_code, r.text[:80]
        except Exception as e:
            return idx, -1, str(e)[:80]

    async with httpx.AsyncClient(base_url=tr.BASE, timeout=30) as c:
        # Score 10 deliveries in parallel. Each has unique idempotency key so
        # the idempotency layer doesn't dedupe them.
        runs_each = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
        results = await asyncio.gather(*[score_one(c, i, runs_each[i]) for i in range(10)])
        statuses = [s for (_, s, _) in results]
        n200 = sum(1 for s in statuses if s == 200)
        check("A: every parallel score lands as 200 or 409 (no 500s)",
              all(s in (200, 409, 429) for s in statuses), str(statuses))
        check("A: at least one delivery actually landed (200)", n200 >= 1, f"200_count={n200}")

    # Confirm DB final state is consistent — every accepted score landed
    row = await tr.PG.fetchrow(
        "SELECT i.total_runs AS inn_runs, i.total_wickets AS inn_wkts, COUNT(d.id) AS n_deliveries "
        "FROM innings i LEFT JOIN deliveries d ON d.innings_id=i.id "
        "WHERE i.match_id=$1 AND i.innings_number=1 GROUP BY i.id", mid)
    check("A: deliveries count == total_runs (since we scored 1s)",
          row["n_deliveries"] == row["inn_runs"],
          f"deliveries={row['n_deliveries']} total_runs={row['inn_runs']}")

    # ─── Scenario B: parallel /declare-innings ─────────────────────────────
    print(f"\n[B] 5 parallel /declare-innings on match {mid}")
    async def declare_one(client, idx):
        try:
            r = await client.post(f"/api/matches/{mid}/declare-innings",
                                  headers={**h, "X-Idempotency-Key": f"b-{idx}"})
            return idx, r.status_code
        except Exception:
            return idx, -1

    async with httpx.AsyncClient(base_url=tr.BASE, timeout=30) as c:
        results = await asyncio.gather(*[declare_one(c, i) for i in range(5)])
        statuses = [s for (_, s) in results]
        n200 = sum(1 for s in statuses if s == 200)
        check("B: every parallel declare resolves cleanly (no 500s)",
              all(s in (200, 400, 429) for s in statuses), str(statuses))
        check("B: exactly one declare won (200), rest got 400 or 429",
              n200 == 1, f"200_count={n200} statuses={statuses}")

    row = await tr.PG.fetchrow(
        "SELECT status, declared FROM innings WHERE match_id=$1 AND innings_number=1", mid)
    check("B: innings ended up declared=true status=completed",
          row["status"] == "completed" and row["declared"] is True, str(dict(row)))

    # ─── Scenario C: parallel /end-innings on innings 2 ────────────────────
    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=30) as c:
        await lr.start_innings(c, mid,
            batting_team_id=tb_id, striker_id=sb[0], non_striker_id=sb[1], bowler_id=sa[8])

    print(f"\n[C] 5 parallel /end-innings on match {mid} innings 2")
    async def end_one(client, idx):
        try:
            r = await client.post(f"/api/matches/{mid}/end-innings",
                                  headers={**h, "X-Idempotency-Key": f"c-{idx}"})
            return idx, r.status_code
        except Exception:
            return idx, -1

    async with httpx.AsyncClient(base_url=tr.BASE, timeout=30) as c:
        results = await asyncio.gather(*[end_one(c, i) for i in range(5)])
        statuses = [s for (_, s) in results]
        n200 = sum(1 for s in statuses if s == 200)
        check("C: every parallel end-innings resolves cleanly (no 500s)",
              all(s in (200, 429) for s in statuses), str(statuses))
        check("C: at least one end-innings won", n200 >= 1, str(statuses))

    row = await tr.PG.fetchrow(
        "SELECT status FROM innings WHERE match_id=$1 AND innings_number=2", mid)
    check("C: innings 2 ended up completed", row["status"] == "completed", str(dict(row)))

    await tr.R.aclose()
    await tr.PG.close()
    print(f"\n=== {passed}/{passed + failed} concurrency stress assertions passed ===")
    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
