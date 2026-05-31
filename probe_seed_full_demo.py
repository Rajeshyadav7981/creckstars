"""Seed a full-pledged demo tournament in account 7981212220 so the user can
explore every screen with realistic data.

Structure:
  • 24 gully-cricket-themed teams, 11 players each (with real Indian names)
  • Stage 1: Group Stage — 4 groups of 6, round-robin = 60 matches
  • Stage 2: Quarter Final — top 2 from each group, cross-paired = 4 matches
  • Stage 3: Semi Final = 2 matches
  • Stage 4: Final = 1 match — engineered to TIE → super over
  Total: 67 main matches + super over

Variety:
  • Per-innings: 3-7 wickets (caught, bowled, lbw, run-out)
  • Boundaries (4s) and sixes (6s) for big scores
  • Star batters scoring 40-80, others getting cheaply out → top-run-scorers shine
  • Catches accumulate into "most catches" stats
"""
import asyncio, sys, random
import asyncpg  # type: ignore[import-not-found]
import httpx  # type: ignore[import-not-found]
import redis.asyncio as redis  # type: ignore[import-not-found]
import tournament_regression as tr
import live_match_regression as lr
from src.utils.security import hash_password

MOBILE = "7981212220"
PASSWORD = "Rajesh@123"
TOURNAMENT_NAME = f"Mumbai Mohalla Premier League {tr.rand_str(3)}"
OVERS_PER_MATCH = 5

GULLY_TEAMS = [
    "Mohalla Maharajas", "Galli Gladiators", "Maidan Mavericks", "Sadak Sehnshah",
    "Tapori Titans", "Bombay Boyz", "Chai Pe Charcha XI", "Park Pavilion",
    "Late Night Loungers", "Local Lions", "No Ball Brothers", "Cheeky Singles",
    "Backstreet 11", "Sixer Sultans", "Yorker Yodhas", "Pitch Pirates",
    "Daddu Dynamos", "Bhaiya Blasters", "Stadium Strays", "Outfield Outlaws",
    "Boundary Beasts", "Wicket Wonders", "Cover Drive Kings", "Spin Sorcerers",
]

# Hand-picked subset — gives variety in player rosters
PLAYER_NAMEBANKS = [lr.INDIA_NAMES, lr.PAK_NAMES, lr.AUS_NAMES] if hasattr(lr, "AUS_NAMES") else [lr.INDIA_NAMES, lr.PAK_NAMES]


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

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        # 1. Create 24 teams with 11 players each
        teams_data = []
        for i, name in enumerate(GULLY_TEAMS):
            t = await lr.db_create_team(f"{name} {tr.rand_str(3)}")
            namebank = PLAYER_NAMEBANKS[i % len(PLAYER_NAMEBANKS)]
            ps = await lr.db_create_players_for_team(t, namebank)
            teams_data.append({"team": t, "players": [p["id"] for p in ps], "name": name})
            print(f"   • team ready ({i+1:>2}/{len(GULLY_TEAMS)}): {name}")

        # 2. Tournament + Stage 1 (group stage with 4 groups of 6)
        t = await tr.mk_tournament(c, TOURNAMENT_NAME, type_="league")
        tid = t["id"]
        print(f"\n   tournament id: {tid}")
        for x in teams_data:
            await tr.add_team(c, tid, x["team"]["id"])

        stages = await tr.setup_stages(c, tid, [
            {"name": "league_matches", "qualification_rule": {"top_n": 2, "from": "each_group"}},
            {"name": "quarter_final", "qualification_rule": {"top_n": 1, "from": "each_group"}},
            {"name": "semi_final", "qualification_rule": {"top_n": 1, "from": "each_group"}},
            {"name": "final"},
        ])
        group_sid = stages[0]["stage_id"] if "stage_id" in stages[0] else stages[0]["id"]
        # Split 24 teams into 4 groups of 6
        group_team_ids = [
            [teams_data[i]["team"]["id"] for i in range(0, 6)],
            [teams_data[i]["team"]["id"] for i in range(6, 12)],
            [teams_data[i]["team"]["id"] for i in range(12, 18)],
            [teams_data[i]["team"]["id"] for i in range(18, 24)],
        ]
        await tr.setup_groups(c, tid, group_sid, [
            {"name": f"Group {chr(65+i)}", "team_ids": group_team_ids[i]}
            for i in range(4)
        ])
        await tr.generate_matches(c, tid, group_sid)
        group_matches = await tr.matches_for_stage(c, tid, group_sid)
        for m in group_matches:
            await c.patch(f"/api/matches/{m['id']}", json={"overs": OVERS_PER_MATCH})
        print(f"\n   ✓ Group Stage: {len(group_matches)} matches generated\n")

        # Helper: play a match with realistic variety
        team_by_id = {x["team"]["id"]: x for x in teams_data}

        async def play_realistic_match(mid, team_a_id, team_b_id, force_winner_idx=None, force_tie=False):
            """Play a 5-over match with random-ish but engineered outcomes.

            force_winner_idx: 0 → team_a wins, 1 → team_b wins, None → random.
            force_tie: target the same total for both innings (used for the Final).
            """
            ta = team_by_id[team_a_id]
            tb = team_by_id[team_b_id]
            await lr.post_toss(c, mid, ta["team"]["id"], "bat")
            await lr.set_squad(c, mid, ta["team"]["id"], ta["players"])
            await lr.set_squad(c, mid, tb["team"]["id"], tb["players"])

            # Pick a winner if not forced
            if force_winner_idx is None and not force_tie:
                force_winner_idx = random.choice([0, 1])

            # Innings totals
            if force_tie:
                target_a = random.randint(40, 60)
                target_b = target_a
            elif force_winner_idx == 0:
                target_a = random.randint(45, 75)
                target_b = target_a - random.randint(3, 25)
            else:
                target_a = random.randint(30, 55)
                target_b = target_a + random.randint(3, 20)
            target_b = max(20, target_b)

            await play_innings(mid, ta, tb, target_a)
            await c.post(f"/api/matches/{mid}/end-innings", headers=h)
            await play_innings(mid, tb, ta, target_b)
            params = "?force_tie=true" if force_tie else ""
            await c.post(f"/api/matches/{mid}/end-match{params}", headers=h)

        async def play_innings(mid, bat, bowl, target_runs):
            """Score around `target_runs` with a mix of singles, boundaries, and wickets."""
            # Pick a star striker who'll score most of the runs
            striker_id = bat["players"][0]
            non_striker_id = bat["players"][1]
            bowler_id = bowl["players"][8]
            await lr.start_innings(c, mid,
                batting_team_id=bat["team"]["id"],
                striker_id=striker_id, non_striker_id=non_striker_id, bowler_id=bowler_id)

            runs_scored = 0
            wickets_fallen = 0
            balls_bowled = 0
            max_balls = OVERS_PER_MATCH * 6
            next_batsman_idx = 2

            while runs_scored < target_runs and balls_bowled < max_balls and wickets_fallen < 9:
                remaining = target_runs - runs_scored
                # Vary the ball: ~10% wicket, ~30% boundary, ~10% six, rest singles/dots
                roll = random.random()
                if roll < 0.08 and wickets_fallen < 8:
                    # Wicket — variety: caught, bowled, lbw, run_out
                    wt = random.choice(["caught", "caught", "bowled", "lbw", "run_out"])
                    payload = {"batsman_runs": 0, "is_wicket": True, "wicket_type": wt}
                    if wt == "caught":
                        # Random fielder from bowling team (not the bowler)
                        fielder = random.choice([p for p in bowl["players"] if p != bowler_id])
                        payload["fielder_id"] = fielder
                    if next_batsman_idx < len(bat["players"]):
                        payload["new_batsman_id"] = bat["players"][next_batsman_idx]
                        next_batsman_idx += 1
                    payload["dismissed_player_id"] = striker_id
                    await _score(mid, payload)
                    wickets_fallen += 1
                    balls_bowled += 1
                    striker_id = payload.get("new_batsman_id", non_striker_id)
                elif roll < 0.20 and remaining >= 6:
                    await _score(mid, {"batsman_runs": 6, "is_six": True})
                    runs_scored += 6
                    balls_bowled += 1
                elif roll < 0.45 and remaining >= 4:
                    await _score(mid, {"batsman_runs": 4, "is_boundary": True})
                    runs_scored += 4
                    balls_bowled += 1
                elif roll < 0.85 and remaining >= 1:
                    runs = 1 if roll < 0.65 else 2
                    runs = min(runs, remaining)
                    await _score(mid, {"batsman_runs": runs})
                    runs_scored += runs
                    balls_bowled += 1
                else:
                    # Dot ball
                    await _score(mid, {"batsman_runs": 0})
                    balls_bowled += 1
                # End of over: advance the bowler (just use a different one)
                if balls_bowled % 6 == 0 and balls_bowled < max_balls:
                    next_bowler = bowl["players"][(8 + (balls_bowled // 6)) % 11]
                    if next_bowler == bowler_id:
                        next_bowler = bowl["players"][(8 + (balls_bowled // 6) + 1) % 11]
                    try:
                        r = await c.post(f"/api/matches/{mid}/end-over",
                                         json={"next_bowler_id": next_bowler}, headers=h)
                        if r.status_code == 200:
                            bowler_id = next_bowler
                    except Exception:
                        pass

        async def _score(mid, body):
            return await c.post(f"/api/matches/{mid}/score",
                                json=body,
                                headers={**h, "X-Idempotency-Key": tr.rand_str(10)})

        # 3. Play all group-stage matches
        print(f"   → Playing {len(group_matches)} group-stage matches…")
        for i, m in enumerate(group_matches):
            await play_realistic_match(m["id"], m["team_a_id"], m["team_b_id"])
            if (i+1) % 10 == 0:
                print(f"     {i+1}/{len(group_matches)} done")
        print(f"   ✓ Group stage complete\n")

        # 4. Refresh tournament to discover the qualified stage info
        await c.post(f"/api/tournaments/{tid}/refresh-stage-states") if False else None
        # Auto-progression: stage 1 should now be complete, qualified teams marked.
        # We just need to call generate_matches for the next stage.

        # Trigger qualification + stage advancement by reading the standings (warms cache)
        await c.get(f"/api/tournaments/{tid}/stages/{group_sid}/standings")
        # Backend marks the stage complete on each end-match, so the next stage exists already.
        # Refresh stages list to get QF stage id.
        tdata = (await c.get(f"/api/tournaments/{tid}")).json()
        all_stages = tdata.get("stages") or []
        def stage_id_by_name(stages, name):
            for s in stages:
                if s.get("stage_name") == name or s.get("name") == name:
                    return s.get("stage_id") or s.get("id")
            return None

        # 5. Quarter Final
        qf_sid = stage_id_by_name(all_stages, "quarter_final")
        if qf_sid:
            await tr.generate_matches(c, tid, qf_sid)
            qf_matches = await tr.matches_for_stage(c, tid, qf_sid)
            for m in qf_matches:
                await c.patch(f"/api/matches/{m['id']}", json={"overs": OVERS_PER_MATCH})
            print(f"   → Playing {len(qf_matches)} quarter-final matches…")
            for m in qf_matches:
                await play_realistic_match(m["id"], m["team_a_id"], m["team_b_id"])
            print(f"   ✓ Quarter Finals complete\n")

        # 6. Semi Final
        tdata = (await c.get(f"/api/tournaments/{tid}")).json()
        all_stages = tdata.get("stages") or []
        sf_sid = stage_id_by_name(all_stages, "semi_final")
        if sf_sid:
            await tr.generate_matches(c, tid, sf_sid)
            sf_matches = await tr.matches_for_stage(c, tid, sf_sid)
            for m in sf_matches:
                await c.patch(f"/api/matches/{m['id']}", json={"overs": OVERS_PER_MATCH})
            print(f"   → Playing {len(sf_matches)} semi-final matches…")
            for m in sf_matches:
                await play_realistic_match(m["id"], m["team_a_id"], m["team_b_id"])
            print(f"   ✓ Semi Finals complete\n")

        # 7. Final — force a tie to trigger super over
        tdata = (await c.get(f"/api/tournaments/{tid}")).json()
        all_stages = tdata.get("stages") or []
        f_sid = stage_id_by_name(all_stages, "final")
        if f_sid:
            await tr.generate_matches(c, tid, f_sid)
            f_matches = await tr.matches_for_stage(c, tid, f_sid)
            for m in f_matches:
                await c.patch(f"/api/matches/{m['id']}", json={"overs": OVERS_PER_MATCH})
            print(f"   → Playing the Final (engineered tie + super over)…")
            for m in f_matches:
                await play_realistic_match(m["id"], m["team_a_id"], m["team_b_id"], force_tie=True)
                # Try to start a super over — if endpoint exists, it'll work; otherwise final ends as tied
                try:
                    ta_id, tb_id = m["team_a_id"], m["team_b_id"]
                    ta = team_by_id[ta_id]; tb = team_by_id[tb_id]
                    # Innings 3 (super over for team A)
                    await lr.start_innings(c, m["id"],
                        batting_team_id=ta_id,
                        striker_id=ta["players"][0], non_striker_id=ta["players"][1],
                        bowler_id=tb["players"][8])
                    for _ in range(6):
                        await _score(m["id"], {"batsman_runs": random.choice([1, 2, 4, 6])})
                    await c.post(f"/api/matches/{m['id']}/end-innings", headers=h)
                    # Innings 4 (super over for team B)
                    await lr.start_innings(c, m["id"],
                        batting_team_id=tb_id,
                        striker_id=tb["players"][0], non_striker_id=tb["players"][1],
                        bowler_id=ta["players"][8])
                    for _ in range(6):
                        await _score(m["id"], {"batsman_runs": random.choice([1, 2, 4, 6])})
                    await c.post(f"/api/matches/{m['id']}/end-match", headers=h)
                except Exception as e:
                    print(f"     (super over flow skipped: {e})")
            print(f"   ✓ Final complete\n")

        # Drain caches
        try:
            await tr.R.delete(f"cache:standings:{tid}")
            await tr.R.delete(f"cache:tournament:{tid}")
        except Exception:
            pass

        # Summary
        std = (await c.get(f"/api/tournaments/{tid}/standings")).json().get("standings") or []
        completed_count = sum(1 for m in group_matches if True)  # all complete by now
        print(f"\n══════ {TOURNAMENT_NAME} ══════")
        print(f"  Tournament id: {tid}")
        print(f"  Login: {MOBILE} / {PASSWORD}")
        print(f"  Open the app → Tourneys tab → search '{TOURNAMENT_NAME[:30]}'")
        print(f"\n  Standings preview (overall, top 5):")
        for i, s in enumerate(std[:5]):
            print(f"    {i+1:>2}. {s['team_name'][:40]:<40} P={s['played']:>2} W={s['won']:>2} NRR={s['nrr']:+6.3f}")
        print()

    await tr.R.aclose()
    await tr.PG.close()


asyncio.run(main())
