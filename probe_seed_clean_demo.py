"""A clean full-pledged demo tournament that goes all the way to a Final
with a super over. Smaller (16 teams) but every stage runs end-to-end so the
user can browse the whole bracket in the UI.

Structure:
  16 gully-cricket teams, 4 groups of 4
  Stage 1: league_matches  → 24 matches, top 1 per group qualifies
  Stage 2: semi_final      → 2 matches (cross-seeded)
  Stage 3: final           → 1 match, engineered to TIE → super over
"""
import asyncio, sys, random
import asyncpg, httpx, redis.asyncio as redis  # type: ignore
import tournament_regression as tr
import live_match_regression as lr
from src.utils.security import hash_password

MOBILE = "7981212220"
PASSWORD = "Rajesh@123"
TOURNAMENT_NAME = f"Galli Cricket Champions Cup {tr.rand_str(3)}"
OVERS_PER_MATCH = 5

GULLY_TEAMS = [
    "Mohalla Maharajas", "Galli Gladiators", "Maidan Mavericks", "Sadak Sehnshah",
    "Tapori Titans", "Bombay Boyz", "Chai Pe Charcha XI", "Park Pavilion",
    "Late Night Loungers", "Local Lions", "No Ball Brothers", "Cheeky Singles",
    "Backstreet 11", "Sixer Sultans", "Yorker Yodhas", "Pitch Pirates",
]


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
    print(f"\n→ Seeding {TOURNAMENT_NAME}\n")

    namebanks = [lr.INDIA_NAMES, lr.PAK_NAMES]

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        teams_data = []
        for i, name in enumerate(GULLY_TEAMS):
            t = await lr.db_create_team(f"{name} {tr.rand_str(3)}")
            ps = await lr.db_create_players_for_team(t, namebanks[i % len(namebanks)])
            teams_data.append({"team": t, "players": [p["id"] for p in ps], "name": name})
            print(f"   • team ({i+1:>2}/16): {name}")

        t = await tr.mk_tournament(c, TOURNAMENT_NAME, type_="league")
        tid = t["id"]
        for x in teams_data:
            await tr.add_team(c, tid, x["team"]["id"])

        await tr.setup_stages(c, tid, [
            {"name": "league_matches", "qualification_rule": {"top_n": 1, "from": "each_group"}},
            {"name": "semi_final"},
            {"name": "final"},
        ])

        stages_rows = await tr.PG.fetch(
            "SELECT id, stage_name FROM tournament_stages WHERE tournament_id=$1 ORDER BY stage_order", tid)
        sid_by_name = {r["stage_name"]: r["id"] for r in stages_rows}

        # 4 groups of 4
        group_team_ids = [
            [teams_data[i]["team"]["id"] for i in range(0, 4)],
            [teams_data[i]["team"]["id"] for i in range(4, 8)],
            [teams_data[i]["team"]["id"] for i in range(8, 12)],
            [teams_data[i]["team"]["id"] for i in range(12, 16)],
        ]
        group_sid = sid_by_name["league_matches"]
        await tr.setup_groups(c, tid, group_sid, [
            {"name": f"Group {chr(65+i)}", "team_ids": group_team_ids[i]} for i in range(4)
        ])
        await tr.generate_matches(c, tid, group_sid)
        team_by_id = {x["team"]["id"]: x for x in teams_data}

        # === Match helpers ===
        async def _score(mid, body):
            await c.post(f"/api/matches/{mid}/score",
                         json=body,
                         headers={**h, "X-Idempotency-Key": tr.rand_str(10)})

        async def play_innings(mid, bat, bowl, target_runs):
            striker_id = bat["players"][0]
            non_striker_id = bat["players"][1]
            bowler_id = bowl["players"][8]
            await lr.start_innings(c, mid,
                batting_team_id=bat["team"]["id"],
                striker_id=striker_id, non_striker_id=non_striker_id, bowler_id=bowler_id)
            runs_scored = wickets_fallen = balls_bowled = 0
            max_balls = OVERS_PER_MATCH * 6
            next_batsman_idx = 2
            while runs_scored < target_runs and balls_bowled < max_balls and wickets_fallen < 9:
                remaining = target_runs - runs_scored
                roll = random.random()
                if roll < 0.10 and wickets_fallen < 8:
                    wt = random.choice(["caught", "caught", "caught", "bowled", "lbw", "run_out"])
                    payload = {"batsman_runs": 0, "is_wicket": True, "wicket_type": wt,
                               "dismissed_player_id": striker_id}
                    if wt == "caught":
                        payload["fielder_id"] = random.choice([p for p in bowl["players"] if p != bowler_id])
                    if next_batsman_idx < len(bat["players"]):
                        payload["new_batsman_id"] = bat["players"][next_batsman_idx]
                        next_batsman_idx += 1
                    await _score(mid, payload)
                    wickets_fallen += 1; balls_bowled += 1
                    striker_id = payload.get("new_batsman_id", non_striker_id)
                elif roll < 0.22 and remaining >= 6:
                    await _score(mid, {"batsman_runs": 6, "is_six": True})
                    runs_scored += 6; balls_bowled += 1
                elif roll < 0.45 and remaining >= 4:
                    await _score(mid, {"batsman_runs": 4, "is_boundary": True})
                    runs_scored += 4; balls_bowled += 1
                elif roll < 0.85 and remaining >= 1:
                    r_ = min(1 if roll < 0.7 else 2, remaining)
                    await _score(mid, {"batsman_runs": r_})
                    runs_scored += r_; balls_bowled += 1
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
                    except Exception: pass

        async def play_match(mid, ta_id, tb_id, force_tie=False):
            ta = team_by_id[ta_id]; tb = team_by_id[tb_id]
            await lr.post_toss(c, mid, ta["team"]["id"], "bat")
            await lr.set_squad(c, mid, ta["team"]["id"], ta["players"])
            await lr.set_squad(c, mid, tb["team"]["id"], tb["players"])
            await c.patch(f"/api/matches/{mid}", json={"overs": OVERS_PER_MATCH})
            winner_idx = random.choice([0, 1])
            if force_tie:
                ta_target = random.randint(40, 55); tb_target = ta_target
            elif winner_idx == 0:
                ta_target = random.randint(45, 65); tb_target = ta_target - random.randint(4, 18)
            else:
                ta_target = random.randint(30, 50); tb_target = ta_target + random.randint(3, 15)
            tb_target = max(15, tb_target)
            await play_innings(mid, ta, tb, ta_target)
            await c.post(f"/api/matches/{mid}/end-innings", headers=h)
            await play_innings(mid, tb, ta, tb_target)
            params = "?force_tie=true" if force_tie else ""
            await c.post(f"/api/matches/{mid}/end-match{params}", headers=h)

        async def play_all_upcoming(sid, label):
            iterations = 0
            while iterations < 5:
                rows = await tr.PG.fetch(
                    "SELECT id, team_a_id, team_b_id FROM matches WHERE stage_id=$1 AND status IN "
                    "('upcoming','scheduled','created','toss') ORDER BY id",
                    sid,
                )
                if not rows: return
                print(f"   → {label}: {len(rows)} to play")
                for r in rows:
                    await play_match(r["id"], r["team_a_id"], r["team_b_id"])
                iterations += 1

        # === Stage 1: Group ===
        await play_all_upcoming(group_sid, "Group Stage")

        # === Stage 2: Semi Final ===
        sf_sid = sid_by_name["semi_final"]
        try: await tr.generate_matches(c, tid, sf_sid)
        except Exception: pass
        await play_all_upcoming(sf_sid, "Semi Final")

        # === Stage 3: Final with forced tie + super over ===
        f_sid = sid_by_name["final"]
        try: await tr.generate_matches(c, tid, f_sid)
        except Exception: pass
        f_matches = await tr.PG.fetch(
            "SELECT id, team_a_id, team_b_id FROM matches WHERE stage_id=$1 AND status IN "
            "('upcoming','scheduled','created','toss')",
            f_sid,
        )
        for fm in f_matches:
            print(f"   → Final (engineered tie): match {fm['id']}")
            await play_match(fm["id"], fm["team_a_id"], fm["team_b_id"], force_tie=True)

            # Super over: 1 over each, innings 3 & 4
            ta = team_by_id[fm["team_a_id"]]; tb = team_by_id[fm["team_b_id"]]
            try:
                await lr.start_innings(c, fm["id"],
                    batting_team_id=ta["team"]["id"],
                    striker_id=ta["players"][0], non_striker_id=ta["players"][1],
                    bowler_id=tb["players"][8])
                so_a = 0
                for _ in range(6):
                    runs = random.choice([1, 2, 4, 6])
                    payload = {"batsman_runs": runs}
                    if runs == 4: payload["is_boundary"] = True
                    if runs == 6: payload["is_six"] = True
                    await _score(fm["id"], payload)
                    so_a += runs
                await c.post(f"/api/matches/{fm['id']}/end-innings", headers=h)

                await lr.start_innings(c, fm["id"],
                    batting_team_id=tb["team"]["id"],
                    striker_id=tb["players"][0], non_striker_id=tb["players"][1],
                    bowler_id=ta["players"][8])
                so_b = 0
                for _ in range(6):
                    runs = random.choice([1, 2, 4, 6])
                    payload = {"batsman_runs": runs}
                    if runs == 4: payload["is_boundary"] = True
                    if runs == 6: payload["is_six"] = True
                    await _score(fm["id"], payload)
                    so_b += runs
                    if so_b > so_a: break
                await c.post(f"/api/matches/{fm['id']}/end-match", headers=h)
                print(f"     super over: A={so_a} B={so_b}")
            except Exception as e:
                print(f"     super over flow error: {e}")

        # Drain caches
        try:
            await tr.R.delete(f"cache:standings:{tid}")
            await tr.R.delete(f"cache:tournament:{tid}")
        except Exception: pass

        std = (await c.get(f"/api/tournaments/{tid}/standings")).json().get("standings") or []
        td = (await c.get(f"/api/tournaments/{tid}")).json()
        n_matches = len(td.get("matches", []))

        print(f"\n══════ {TOURNAMENT_NAME} ══════")
        print(f"  Tournament id: {tid}")
        print(f"  Total matches:  {n_matches}")
        print(f"  Login:          {MOBILE} / {PASSWORD}\n")
        print(f"  Standings — overall top 5:")
        for i, s in enumerate(std[:5]):
            print(f"    {i+1:>2}. {s['team_name'][:36]:<36} P={s['played']:>2} W={s['won']:>2} L={s['lost']:>2} NRR={s['nrr']:+6.3f}")
        print()

    await tr.R.aclose()
    await tr.PG.close()


asyncio.run(main())
