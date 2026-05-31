"""Net Run Rate tiebreaker — when teams tie on wins, qualification must be
decided by NRR.

Scenario:
  5-team round-robin group, top 3 qualify.
  Engineer the wins so teams 2, 3, 4 end with the same W/L record.
  Team 2 wins by a huge margin (high NRR), team 4 wins narrowly (low NRR).
  Top 3 must come out as [Team A, Team B, Team C] in NRR order — NOT D.
"""
import asyncio, sys
import asyncpg  # type: ignore[import-not-found]
import httpx  # type: ignore[import-not-found]
import redis.asyncio as redis  # type: ignore[import-not-found]
import tournament_regression as tr
import live_match_regression as lr
from src.utils.security import hash_password
from src.services.cricket_rules import overs_to_balls, nrr_for_team

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

    print("\n[unit] overs_to_balls + nrr_for_team math")
    check("4.3 overs == 27 balls", overs_to_balls(4.3) == 27, f"got {overs_to_balls(4.3)}")
    check("5.5 overs == 35 balls", overs_to_balls(5.5) == 35, f"got {overs_to_balls(5.5)}")
    check("10.0 overs == 60 balls", overs_to_balls(10.0) == 60, f"got {overs_to_balls(10.0)}")
    check("None → 0 balls (no innings)", overs_to_balls(None) == 0)
    check("0.0 overs → 0 balls", overs_to_balls(0.0) == 0)
    check("Bug-flag: 4.3 + 5.2 in DISPLAY overs would be 9.5",
          4.3 + 5.2 == 9.5)
    check("But proper-overs: 27 + 32 balls = 59 balls = 9.83 overs (not 9.5)",
          abs((27 + 32) / 6 - 9.833) < 0.01)
    # NRR sanity: 100 in 27 balls vs 80 in 27 balls
    # rate_for  = 100 * 6 / 27 = 22.222
    # rate_ag   =  80 * 6 / 27 = 17.778
    # nrr       =  4.444
    check("NRR(27, 100, 27, 80) ≈ 4.444",
          abs(nrr_for_team(27, 100, 27, 80) - 4.444) < 0.001,
          f"got {nrr_for_team(27, 100, 27, 80)}")
    check("NRR with zero balls = 0 (no divide-by-zero)",
          nrr_for_team(0, 0, 0, 0) == 0.0)

    print("\n[e2e] 5-team group, top 3 qualify, ties on wins")

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        # Create 5 teams with 11 players each
        teams_data = []
        for letter in ['A', 'B', 'C', 'D', 'E']:
            t = await lr.db_create_team(f"NRR-{letter}-{tr.rand_str(3)}")
            ps = await lr.db_create_players_for_team(t, lr.INDIA_NAMES)
            teams_data.append({"team": t, "players": [p["id"] for p in ps]})

        t = await tr.mk_tournament(c, f"NRR-{tr.rand_str(4)}", type_="league")
        tid = t["id"]
        for x in teams_data:
            await tr.add_team(c, tid, x["team"]["id"])

        stages = await tr.setup_stages(c, tid, [{
            "name": "group_stage",
            "qualification_rule": {"top_n": 3, "from": "each_group"},
        }])
        sid = stages[0]["id"]
        all_team_ids = [x["team"]["id"] for x in teams_data]
        await tr.setup_groups(c, tid, sid, [{"name": "Group A", "team_ids": all_team_ids}])
        await tr.generate_matches(c, tid, sid)
        ms = await tr.matches_for_stage(c, tid, sid)
        # All matches are 10-over format so scores stay short
        for m in ms:
            r = await c.patch(f"/api/matches/{m['id']}", json={"overs": 10}); r.raise_for_status()

        # Helper: score a match where the winner has a markedly higher run rate
        # than the loser. We score the winner's runs as boundaries (4s) and the
        # loser's runs as singles — so the same runs use very different ball counts
        # and the NRR calculation must distinguish them.
        async def score_runs(mid, balls_per_run, total_runs):
            """Score `total_runs` worth of runs as either 4s or 1s."""
            if balls_per_run == 1:
                for _ in range(total_runs):
                    await c.post(f"/api/matches/{mid}/score", json={"batsman_runs": 1},
                                 headers={**h, "X-Idempotency-Key": tr.rand_str(8)})
            else:
                fours = total_runs // 4
                rem = total_runs - fours * 4
                for _ in range(fours):
                    await c.post(f"/api/matches/{mid}/score",
                                 json={"batsman_runs": 4, "is_boundary": True},
                                 headers={**h, "X-Idempotency-Key": tr.rand_str(8)})
                for _ in range(rem):
                    await c.post(f"/api/matches/{mid}/score", json={"batsman_runs": 1},
                                 headers={**h, "X-Idempotency-Key": tr.rand_str(8)})

        async def play_match(mid, bat_idx, bowl_idx, bat_runs, bowl_runs, fast_winner=True):
            """fast_winner: the winning side scores in 4s (fewer balls → higher RR);
            the losing side scores in 1s (more balls → lower RR)."""
            bat = teams_data[bat_idx]
            bowl = teams_data[bowl_idx]
            await lr.post_toss(c, mid, bat["team"]["id"], "bat")
            await lr.set_squad(c, mid, bat["team"]["id"], bat["players"])
            await lr.set_squad(c, mid, bowl["team"]["id"], bowl["players"])
            await lr.start_innings(c, mid,
                batting_team_id=bat["team"]["id"],
                striker_id=bat["players"][0], non_striker_id=bat["players"][1],
                bowler_id=bowl["players"][8])
            bat_wins = bat_runs > bowl_runs
            await score_runs(mid, 4 if (fast_winner and bat_wins) else 1, bat_runs)
            await c.post(f"/api/matches/{mid}/end-innings", headers=h)
            await lr.start_innings(c, mid,
                batting_team_id=bowl["team"]["id"],
                striker_id=bowl["players"][0], non_striker_id=bowl["players"][1],
                bowler_id=bat["players"][8])
            await score_runs(mid, 4 if (fast_winner and not bat_wins) else 1, bowl_runs)
            await c.post(f"/api/matches/{mid}/end-match", headers=h)

        # Map each matchup so we can pick the right match id by (team_a_idx, team_b_idx)
        def find(a_idx, b_idx):
            a = teams_data[a_idx]["team"]["id"]
            b = teams_data[b_idx]["team"]["id"]
            for m in ms:
                if {m["team_a_id"], m["team_b_id"]} == {a, b}:
                    return m["id"]
            return None

        # Engineered scenario:
        #   A wins ALL its games (4 wins, top of table)
        #   B, C, D each win 2 games and lose 2 → all on equal wins
        #   E loses all (0 wins, bottom)
        # NRR engineered: B's wins are by HUGE margin, D's wins are barely
        #   so qualified top-3 must be A, B, C (D eliminated).
        #
        # 10 matches total in a 5-team RR.
        plays = [
            # (mid_lookup, bat_idx, bowl_idx, bat_runs, bowl_runs)
            # A wins all 4
            (find(0, 1), 0, 1, 25, 5),     # A beat B by 20
            (find(0, 2), 0, 2, 25, 5),     # A beat C by 20
            (find(0, 3), 0, 3, 25, 5),     # A beat D by 20
            (find(0, 4), 0, 4, 25, 5),     # A beat E by 20

            # B beats C and E by HUGE margins (high NRR)
            (find(1, 2), 1, 2, 50, 2),     # B beat C by 48
            (find(1, 4), 1, 4, 50, 2),     # B beat E by 48
            # B loses to D narrowly
            (find(1, 3), 3, 1, 30, 25),    # D beat B by 5

            # C beats D and E by moderate margin
            (find(2, 3), 2, 3, 30, 18),    # C beat D by 12
            (find(2, 4), 2, 4, 30, 18),    # C beat E by 12

            # D's only other win against E by 1 run (lowest NRR among the tied)
            (find(3, 4), 3, 4, 20, 19),    # D beat E by 1
        ]
        for mid, ba, bo, br, ow in plays:
            assert mid, "match lookup failed"
            await play_match(mid, ba, bo, br, ow)

        # Fetch standings via the public API
        st_res = await c.get(f"/api/tournaments/{tid}/standings")
        st_res.raise_for_status()
        rows = st_res.json().get("standings") or []
        order = [r["team_name"] for r in rows]
        wins = {r["team_name"]: r["won"] for r in rows}
        nrrs = {r["team_name"]: r["nrr"] for r in rows}
        quals = {r["team_name"]: r.get("qualification_status") for r in rows}
        print(f"\n   standings order: {order}")
        print(f"   wins:        {wins}")
        print(f"   nrrs:        {nrrs}")
        print(f"   qualified:   {quals}")

        name = lambda i: teams_data[i]["team"]["name"]

        check("Team A is first (4 wins)", order[0] == name(0), f"got {order[0]}")
        check("Team E is last (0 wins)", order[-1] == name(4), f"got {order[-1]}")
        check("Teams B/C/D are middle in positions 2..4",
              set(order[1:4]) == {name(1), name(2), name(3)},
              f"got {order[1:4]}")
        check("Three middle teams tied on wins (all 2)",
              wins[name(1)] == 2 and wins[name(2)] == 2 and wins[name(3)] == 2,
              f"B={wins[name(1)]} C={wins[name(2)]} D={wins[name(3)]}")
        check("Team B's NRR > Team C's NRR > Team D's NRR (engineered)",
              nrrs[name(1)] > nrrs[name(2)] > nrrs[name(3)],
              f"B={nrrs[name(1)]} C={nrrs[name(2)]} D={nrrs[name(3)]}")
        check("Top-3 ordering is A → B → C (NRR-correct)",
              order[0] == name(0) and order[1] == name(1) and order[2] == name(2),
              f"got {order[:3]}")
        check("Team D is FOURTH (eliminated despite tying B/C on wins)",
              order[3] == name(3), f"got {order[3]}")

        # Now verify the qualification flag matches the standings — top 3 should
        # be flagged "qualified" by the backend, fourth must be eliminated.
        check("Team A qualified", quals.get(name(0)) == "qualified")
        check("Team B qualified (won via NRR tiebreak)", quals.get(name(1)) == "qualified")
        check("Team C qualified (won via NRR tiebreak)", quals.get(name(2)) == "qualified")
        check("Team D NOT qualified (lost NRR tiebreak)",
              quals.get(name(3)) != "qualified",
              f"got status={quals.get(name(3))}")

    await tr.R.aclose()
    await tr.PG.close()
    print(f"\n=== {passed}/{passed + failed} NRR-tiebreaker assertions passed ===")
    sys.exit(0 if failed == 0 else 1)


asyncio.run(main())
