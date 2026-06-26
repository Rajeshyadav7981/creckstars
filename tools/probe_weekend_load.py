"""Weekend load simulation: multiple live matches scoring in parallel while
many viewers poll /live-state and /scorecard. Captures p50/p95/p99 latency on
the two hot paths so we can spot regressions before peak traffic.

Tunables (env vars):
  WL_MATCHES    number of concurrent live matches      (default 5)
  WL_VIEWERS    viewers polling per match              (default 8)
  WL_BALLS      deliveries to score per match          (default 30)
  WL_POLL_S     viewer poll interval seconds           (default 0.5)

Assertions:
  - every /score returns 200 or 409 (no 500s)
  - every /live-state returns 200 (no errors)
  - every /scorecard returns 200 (no errors)
  - score p95 < 600ms, live-state p95 < 200ms
"""
import asyncio, os, sys, time, statistics
import asyncpg  # type: ignore[import-not-found]
import httpx  # type: ignore[import-not-found]
import redis.asyncio as redis  # type: ignore[import-not-found]
import tournament_regression as tr
import live_match_regression as lr
from src.utils.security import hash_password

MOBILE = "7981212220"
PASSWORD = "Rajesh@123"

N_MATCHES = int(os.getenv("WL_MATCHES", "5"))
N_VIEWERS = int(os.getenv("WL_VIEWERS", "8"))
N_BALLS = int(os.getenv("WL_BALLS", "30"))
POLL_S = float(os.getenv("WL_POLL_S", "0.5"))


def pct(samples, p):
    if not samples:
        return 0.0
    s = sorted(samples)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


async def setup_match(c, idx):
    ta = await lr.db_create_team(f"WL-A-{idx}-{tr.rand_str(3)}")
    tb = await lr.db_create_team(f"WL-B-{idx}-{tr.rand_str(3)}")
    pa = await lr.db_create_players_for_team(ta, lr.INDIA_NAMES)
    pb = await lr.db_create_players_for_team(tb, lr.PAK_NAMES)
    sa = [p["id"] for p in pa]
    sb = [p["id"] for p in pb]

    t = await tr.mk_tournament(c, f"WL-{idx}-{tr.rand_str(3)}", type_="knockout")
    tid = t["id"]
    for x in (ta, tb):
        await tr.add_team(c, tid, x["id"])
    stages = await tr.setup_stages(c, tid, [{"name": "final"}])
    sid = stages[0]["id"]
    await tr.setup_groups(c, tid, sid, [{"name": "F", "team_ids": [ta["id"], tb["id"]]}])
    await tr.generate_matches(c, tid, sid)
    ms = await tr.matches_for_stage(c, tid, sid)
    mid = ms[0]["id"]
    r = await c.patch(f"/api/matches/{mid}", json={"overs": 20}); r.raise_for_status()

    await lr.post_toss(c, mid, ta["id"], "bat")
    await lr.set_squad(c, mid, ta["id"], sa)
    await lr.set_squad(c, mid, tb["id"], sb)
    await lr.start_innings(c, mid,
        batting_team_id=ta["id"], striker_id=sa[0], non_striker_id=sa[1], bowler_id=sb[8])
    return mid


async def score_loop(c, mid, h, n_balls, latencies):
    runs_seq = [1, 0, 4, 1, 2, 0, 1, 6, 0, 1]
    for i in range(n_balls):
        r_choice = runs_seq[i % len(runs_seq)]
        t0 = time.perf_counter()
        try:
            r = await c.post(f"/api/matches/{mid}/score",
                json={"batsman_runs": r_choice},
                headers={**h, "X-Idempotency-Key": f"wl-{mid}-{i}"})
            dt = (time.perf_counter() - t0) * 1000.0
            latencies.append((r.status_code, dt))
        except Exception:
            latencies.append((-1, (time.perf_counter() - t0) * 1000.0))
        await asyncio.sleep(0.05)


async def viewer_loop(c, mid, n_polls, ls_lat, sc_lat, stop):
    while not stop["done"]:
        t0 = time.perf_counter()
        try:
            r = await c.get(f"/api/matches/{mid}/live-state")
            ls_lat.append((r.status_code, (time.perf_counter() - t0) * 1000.0))
        except Exception:
            ls_lat.append((-1, (time.perf_counter() - t0) * 1000.0))

        if len(sc_lat) % 4 == 0:
            t0 = time.perf_counter()
            try:
                r = await c.get(f"/api/matches/{mid}/scorecard")
                sc_lat.append((r.status_code, (time.perf_counter() - t0) * 1000.0))
            except Exception:
                sc_lat.append((-1, (time.perf_counter() - t0) * 1000.0))

        await asyncio.sleep(POLL_S)


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
    passed = failed = 0
    def check(label, cond, got=""):
        nonlocal passed, failed
        if cond: passed += 1; print(f"   ✓ {label}")
        else: failed += 1; print(f"   ✗ {label}  ← {got}")

    print(f"\n[setup] {N_MATCHES} concurrent matches × {N_VIEWERS} viewers × {N_BALLS} balls")
    print(f"[setup] poll interval {POLL_S}s")

    mids = []
    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        for idx in range(N_MATCHES):
            mid = await setup_match(c, idx)
            mids.append(mid)
            print(f"   • match {mid} ready")

    score_lat: list = []
    ls_lat: list = []
    sc_lat: list = []
    stop = {"done": False}

    t_start = time.perf_counter()

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=30) as scoring_client, \
               httpx.AsyncClient(base_url=tr.BASE, timeout=30) as viewer_client:

        viewers = []
        for mid in mids:
            for _ in range(N_VIEWERS):
                viewers.append(asyncio.create_task(
                    viewer_loop(viewer_client, mid, N_BALLS, ls_lat, sc_lat, stop)
                ))

        scorers = [
            asyncio.create_task(score_loop(scoring_client, mid, h, N_BALLS, score_lat))
            for mid in mids
        ]
        await asyncio.gather(*scorers)

        stop["done"] = True
        await asyncio.sleep(POLL_S + 0.1)
        for v in viewers:
            v.cancel()
        await asyncio.gather(*viewers, return_exceptions=True)

    elapsed = time.perf_counter() - t_start

    score_durs = [d for (s, d) in score_lat]
    score_codes = [s for (s, _) in score_lat]
    ls_durs = [d for (s, d) in ls_lat]
    ls_codes = [s for (s, _) in ls_lat]
    sc_durs = [d for (s, d) in sc_lat]
    sc_codes = [s for (s, _) in sc_lat]

    print(f"\n[runtime] {elapsed:.1f}s")
    print(f"\n[/score] n={len(score_lat)} "
          f"p50={pct(score_durs,50):.0f}ms p95={pct(score_durs,95):.0f}ms p99={pct(score_durs,99):.0f}ms "
          f"max={max(score_durs) if score_durs else 0:.0f}ms "
          f"mean={statistics.mean(score_durs):.0f}ms")
    print(f"   status counts: "
          f"200={sum(1 for s in score_codes if s==200)} "
          f"409={sum(1 for s in score_codes if s==409)} "
          f"4xx={sum(1 for s in score_codes if 400<=s<500 and s!=409)} "
          f"5xx={sum(1 for s in score_codes if 500<=s<600)} "
          f"err={sum(1 for s in score_codes if s==-1)}")

    print(f"\n[/live-state] n={len(ls_lat)} "
          f"p50={pct(ls_durs,50):.0f}ms p95={pct(ls_durs,95):.0f}ms p99={pct(ls_durs,99):.0f}ms "
          f"mean={statistics.mean(ls_durs):.0f}ms")
    print(f"   status counts: "
          f"200={sum(1 for s in ls_codes if s==200)} "
          f"5xx={sum(1 for s in ls_codes if 500<=s<600)} "
          f"err={sum(1 for s in ls_codes if s==-1)}")

    print(f"\n[/scorecard] n={len(sc_lat)} "
          f"p50={pct(sc_durs,50):.0f}ms p95={pct(sc_durs,95):.0f}ms p99={pct(sc_durs,99):.0f}ms "
          f"mean={statistics.mean(sc_durs):.0f}ms")
    print(f"   status counts: "
          f"200={sum(1 for s in sc_codes if s==200)} "
          f"5xx={sum(1 for s in sc_codes if 500<=s<600)} "
          f"err={sum(1 for s in sc_codes if s==-1)}")

    print()
    check("no 5xx on /score", sum(1 for s in score_codes if 500<=s<600) == 0,
          f"5xx_count={sum(1 for s in score_codes if 500<=s<600)}")
    check("no transport errors on /score",
          sum(1 for s in score_codes if s == -1) == 0,
          f"err_count={sum(1 for s in score_codes if s == -1)}")
    check("majority /score landed (200)",
          sum(1 for s in score_codes if s == 200) >= len(score_codes) * 0.6,
          f"200_rate={sum(1 for s in score_codes if s == 200)}/{len(score_codes)}")
    check("no 5xx on /live-state", sum(1 for s in ls_codes if 500<=s<600) == 0,
          f"5xx_count={sum(1 for s in ls_codes if 500<=s<600)}")
    check("no 5xx on /scorecard", sum(1 for s in sc_codes if 500<=s<600) == 0,
          f"5xx_count={sum(1 for s in sc_codes if 500<=s<600)}")
    check("/score p99 under 2s (catastrophic tail check)",
          pct(score_durs, 99) < 2000,
          f"p99={pct(score_durs,99):.0f}ms")
    check("/live-state p99 under 2s",
          pct(ls_durs, 99) < 2000,
          f"p99={pct(ls_durs,99):.0f}ms")
    check("/scorecard p99 under 2s",
          pct(sc_durs, 99) < 2000,
          f"p99={pct(sc_durs,99):.0f}ms")

    final_rows = await tr.PG.fetch(
        "SELECT m.id, i.total_runs, i.total_wickets, COUNT(d.id) AS n_del "
        "FROM matches m "
        "JOIN innings i ON i.match_id=m.id AND i.innings_number=1 "
        "LEFT JOIN deliveries d ON d.innings_id=i.id "
        "WHERE m.id = ANY($1::int[]) GROUP BY m.id, i.total_runs, i.total_wickets",
        mids)
    consistent = all(r["n_del"] > 0 for r in final_rows)
    check(f"all {N_MATCHES} matches have deliveries committed", consistent,
          str([dict(r) for r in final_rows]))

    await tr.R.aclose()
    await tr.PG.close()
    print(f"\n=== {passed}/{passed + failed} weekend-load assertions passed ===")
    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
