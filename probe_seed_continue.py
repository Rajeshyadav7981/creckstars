"""Continue seeding tournament 344 — play remaining group matches, then QF/SF/Final.
Re-uses the existing teams + players. Idempotent: only acts on upcoming matches."""
import asyncio, sys, random
import asyncpg  # type: ignore[import-not-found]
import httpx  # type: ignore[import-not-found]
import redis.asyncio as redis  # type: ignore[import-not-found]
import tournament_regression as tr
import live_match_regression as lr
from src.utils.security import hash_password

MOBILE = "7981212220"
PASSWORD = "Rajesh@123"
TOURNAMENT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 344
OVERS_PER_MATCH = 5


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

    # Build team→player map from DB squad data (avoids re-seeding)
    rows = await tr.PG.fetch("""
        SELECT t.id AS team_id, t.name, p.id AS player_id
        FROM teams t
        JOIN team_players tp ON tp.team_id = t.id
        JOIN players p ON p.id = tp.player_id
        WHERE t.id IN (
            SELECT team_id FROM tournament_teams WHERE tournament_id = $1
        )
        ORDER BY t.id, p.id
    """, TOURNAMENT_ID)
    team_by_id = {}
    for r in rows:
        tid_, name, pid = r["team_id"], r["name"], r["player_id"]
        if tid_ not in team_by_id:
            team_by_id[tid_] = {"team": {"id": tid_, "name": name}, "players": [], "name": name}
        team_by_id[tid_]["players"].append(pid)
    print(f"Loaded {len(team_by_id)} teams, total players: {sum(len(t['players']) for t in team_by_id.values())}")

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:

        async def _score(mid, body):
            return await c.post(f"/api/matches/{mid}/score",
                                json=body,
                                headers={**h, "X-Idempotency-Key": tr.rand_str(10)})

        async def play_innings(mid, bat, bowl, target_runs):
            striker_id = bat["players"][0]
            non_striker_id = bat["players"][1]
            bowler_id = bowl["players"][8]
            await lr.start_innings(c, mid,
                batting_team_id=bat["team"]["id"],
                striker_id=striker_id, non_striker_id=non_striker_id, bowler_id=bowler_id)

            runs_scored = 0; wickets_fallen = 0; balls_bowled = 0
            max_balls = OVERS_PER_MATCH * 6
            next_batsman_idx = 2

            while runs_scored < target_runs and balls_bowled < max_balls and wickets_fallen < 9:
                remaining = target_runs - runs_scored
                roll = random.random()
                if roll < 0.08 and wickets_fallen < 8:
                    wt = random.choice(["caught", "caught", "bowled", "lbw", "run_out"])
                    payload = {"batsman_runs": 0, "is_wicket": True, "wicket_type": wt}
                    if wt == "caught":
                        fielder = random.choice([p for p in bowl["players"] if p != bowler_id])
                        payload["fielder_id"] = fielder
                    if next_batsman_idx < len(bat["players"]):
                        payload["new_batsman_id"] = bat["players"][next_batsman_idx]
                        next_batsman_idx += 1
                    payload["dismissed_player_id"] = striker_id
                    await _score(mid, payload)
                    wickets_fallen += 1; balls_bowled += 1
                    striker_id = payload.get("new_batsman_id", non_striker_id)
                elif roll < 0.20 and remaining >= 6:
                    await _score(mid, {"batsman_runs": 6, "is_six": True})
                    runs_scored += 6; balls_bowled += 1
                elif roll < 0.45 and remaining >= 4:
                    await _score(mid, {"batsman_runs": 4, "is_boundary": True})
                    runs_scored += 4; balls_bowled += 1
                elif roll < 0.85 and remaining >= 1:
                    runs = 1 if roll < 0.65 else 2
                    runs = min(runs, remaining)
                    await _score(mid, {"batsman_runs": runs})
                    runs_scored += runs; balls_bowled += 1
                else:
                    await _score(mid, {"batsman_runs": 0})
                    balls_bowled += 1
                if balls_bowled % 6 == 0 and balls_bowled < max_balls:
                    next_bowler = bowl["players"][(8 + (balls_bowled // 6)) % 11]
                    if next_bowler == bowler_id:
                        next_bowler = bowl["players"][(8 + (balls_bowled // 6) + 1) % 11]
                    try:
                        rr = await c.post(f"/api/matches/{mid}/end-over",
                                          json={"next_bowler_id": next_bowler}, headers=h)
                        if rr.status_code == 200: bowler_id = next_bowler
                    except Exception:
                        pass

        async def play_match(mid, team_a_id, team_b_id, force_tie=False, force_winner_idx=None):
            ta = team_by_id.get(team_a_id)
            tb = team_by_id.get(team_b_id)
            if not ta or not tb:
                print(f"   ! skip match {mid}: missing team data")
                return
            await lr.post_toss(c, mid, ta["team"]["id"], "bat")
            await lr.set_squad(c, mid, ta["team"]["id"], ta["players"])
            await lr.set_squad(c, mid, tb["team"]["id"], tb["players"])

            if force_winner_idx is None and not force_tie:
                force_winner_idx = random.choice([0, 1])
            if force_tie:
                target_a = random.randint(40, 60); target_b = target_a
            elif force_winner_idx == 0:
                target_a = random.randint(45, 75); target_b = target_a - random.randint(3, 25)
            else:
                target_a = random.randint(30, 55); target_b = target_a + random.randint(3, 20)
            target_b = max(20, target_b)

            await play_innings(mid, ta, tb, target_a)
            await c.post(f"/api/matches/{mid}/end-innings", headers=h)
            await play_innings(mid, tb, ta, target_b)
            params = "?force_tie=true" if force_tie else ""
            await c.post(f"/api/matches/{mid}/end-match{params}", headers=h)

        async def play_all_upcoming_in_stage(stage_id, label):
            """Play upcoming matches until none remain in stage_id."""
            iterations = 0
            while True:
                rows = await tr.PG.fetch("""
                    SELECT id, team_a_id, team_b_id FROM matches
                    WHERE stage_id = $1 AND status IN ('upcoming','scheduled','created','toss')
                    ORDER BY id LIMIT 50
                """, stage_id)
                if not rows:
                    return
                print(f"   → {label}: {len(rows)} matches to play (iter {iterations+1})")
                for r in rows:
                    await play_match(r["id"], r["team_a_id"], r["team_b_id"])
                iterations += 1
                if iterations > 4: return  # safety

        async def stages_by_name():
            rows = await tr.PG.fetch(
                "SELECT id, stage_name FROM tournament_stages WHERE tournament_id = $1 ORDER BY stage_order",
                TOURNAMENT_ID,
            )
            return {r["stage_name"]: r["id"] for r in rows}

        # Stage 1: finish remaining group matches
        sbn = await stages_by_name()
        group_sid = sbn.get("league_matches")
        await play_all_upcoming_in_stage(group_sid, "Group Stage cleanup")

        # Stage 2: Quarter Final
        qf_sid = sbn.get("quarter_final")
        if qf_sid:
            # Trigger qualification + generation
            try:
                await tr.generate_matches(c, TOURNAMENT_ID, qf_sid)
            except Exception:
                pass  # may already be auto-generated
            for m in await tr.PG.fetch("SELECT id FROM matches WHERE stage_id=$1", qf_sid):
                await c.patch(f"/api/matches/{m['id']}", json={"overs": OVERS_PER_MATCH})
            await play_all_upcoming_in_stage(qf_sid, "Quarter Final")

        # Stage 3: Semi Final
        sbn = await stages_by_name()
        sf_sid = sbn.get("semi_final")
        if sf_sid:
            try:
                await tr.generate_matches(c, TOURNAMENT_ID, sf_sid)
            except Exception:
                pass
            for m in await tr.PG.fetch("SELECT id FROM matches WHERE stage_id=$1", sf_sid):
                await c.patch(f"/api/matches/{m['id']}", json={"overs": OVERS_PER_MATCH})
            await play_all_upcoming_in_stage(sf_sid, "Semi Final")

        # Stage 4: Final — engineered tie + super over
        sbn = await stages_by_name()
        f_sid = sbn.get("final")
        if f_sid:
            try:
                await tr.generate_matches(c, TOURNAMENT_ID, f_sid)
            except Exception:
                pass
            f_matches = await tr.PG.fetch(
                "SELECT id, team_a_id, team_b_id FROM matches WHERE stage_id=$1 AND status IN ('upcoming','scheduled','created','toss')",
                f_sid,
            )
            for fm in f_matches:
                await c.patch(f"/api/matches/{fm['id']}", json={"overs": OVERS_PER_MATCH})
                print(f"   → Final (engineered tie): match {fm['id']}")
                await play_match(fm["id"], fm["team_a_id"], fm["team_b_id"], force_tie=True)
                # Super over: 1 over each, 3rd & 4th innings
                ta = team_by_id[fm["team_a_id"]]; tb = team_by_id[fm["team_b_id"]]
                try:
                    await lr.start_innings(c, fm["id"],
                        batting_team_id=ta["team"]["id"],
                        striker_id=ta["players"][0], non_striker_id=ta["players"][1],
                        bowler_id=tb["players"][8])
                    so_runs_a = 0
                    for _ in range(6):
                        runs = random.choice([1, 2, 4, 6])
                        await _score(fm["id"], {"batsman_runs": runs,
                                                **({"is_boundary": True} if runs == 4 else {}),
                                                **({"is_six": True} if runs == 6 else {})})
                        so_runs_a += runs
                    await c.post(f"/api/matches/{fm['id']}/end-innings", headers=h)

                    await lr.start_innings(c, fm["id"],
                        batting_team_id=tb["team"]["id"],
                        striker_id=tb["players"][0], non_striker_id=tb["players"][1],
                        bowler_id=ta["players"][8])
                    so_runs_b = 0
                    # Make team B chase: score until they exceed so_runs_a, then end
                    for _ in range(6):
                        runs = random.choice([1, 2, 4, 6])
                        await _score(fm["id"], {"batsman_runs": runs,
                                                **({"is_boundary": True} if runs == 4 else {}),
                                                **({"is_six": True} if runs == 6 else {})})
                        so_runs_b += runs
                        if so_runs_b > so_runs_a: break
                    await c.post(f"/api/matches/{fm['id']}/end-match", headers=h)
                    print(f"     super over: A={so_runs_a} B={so_runs_b}")
                except Exception as e:
                    print(f"     super over flow error: {e}")

        # Cache drain
        try:
            await tr.R.delete(f"cache:standings:{TOURNAMENT_ID}")
            await tr.R.delete(f"cache:tournament:{TOURNAMENT_ID}")
        except Exception:
            pass

        std = (await c.get(f"/api/tournaments/{TOURNAMENT_ID}/standings")).json().get("standings") or []
        print(f"\n══════ Tournament {TOURNAMENT_ID} ══════")
        print(f"  Final standings — top 5:")
        for i, s in enumerate(std[:5]):
            print(f"    {i+1:>2}. {s['team_name'][:40]:<40} P={s['played']:>2} W={s['won']:>2} NRR={s['nrr']:+6.3f}")
        print()

    await tr.R.aclose()
    await tr.PG.close()


asyncio.run(main())
