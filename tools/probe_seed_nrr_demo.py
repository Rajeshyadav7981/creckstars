"""Seed a real tournament in the 7981212220 account so the NRR tiebreaker
is visible in the UI. 5 teams, top-3 qualify, B / C / D tied on wins with
clearly different NRRs.

Run once. Look for the tournament titled "NRR Tiebreak Demo" in the app.
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
TOURNAMENT_NAME = f"NRR Tiebreak Demo {tr.rand_str(4)}"


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
    print(f"\n→ Seeding tournament: {TOURNAMENT_NAME}")

    async with httpx.AsyncClient(base_url=tr.BASE, headers=h, timeout=60) as c:
        # 5 teams with 11 players each
        team_names = ["Hyderabad Lions", "Bangalore Tigers", "Mumbai Strikers", "Delhi Knights", "Chennai Royals"]
        teams_data = []
        for tn in team_names:
            t = await lr.db_create_team(f"{tn} {tr.rand_str(3)}")
            ps = await lr.db_create_players_for_team(t, lr.INDIA_NAMES)
            teams_data.append({"team": t, "players": [p["id"] for p in ps]})
            print(f"   • team ready: {t['name']}")

        t = await tr.mk_tournament(c, TOURNAMENT_NAME, type_="league")
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
        for m in ms:
            await c.patch(f"/api/matches/{m['id']}", json={"overs": 10})

        print(f"   • {len(ms)} matches generated (round-robin)\n→ Playing matches…")

        async def score_runs(mid, balls_per_run, total_runs):
            """balls_per_run = 1 → singles; balls_per_run = 4 → boundaries (4 per ball)."""
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

        async def play_match(mid, bat_idx, bowl_idx, bat_runs, bowl_runs):
            """Winner scores via boundaries (fewer balls → higher run rate);
            loser scores via singles (more balls → lower run rate). This guarantees
            the NRR contributions of teams differ even at the same total runs."""
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
            await score_runs(mid, 4 if bat_wins else 1, bat_runs)
            await c.post(f"/api/matches/{mid}/end-innings", headers=h)
            await lr.start_innings(c, mid,
                batting_team_id=bowl["team"]["id"],
                striker_id=bowl["players"][0], non_striker_id=bowl["players"][1],
                bowler_id=bat["players"][8])
            await score_runs(mid, 4 if not bat_wins else 1, bowl_runs)
            await c.post(f"/api/matches/{mid}/end-match", headers=h)

        def find(a_idx, b_idx):
            a = teams_data[a_idx]["team"]["id"]
            b = teams_data[b_idx]["team"]["id"]
            for m in ms:
                if {m["team_a_id"], m["team_b_id"]} == {a, b}:
                    return m["id"]
            return None

        # Hyderabad (0): wins everything — top of table
        # Bangalore (1):  2W, 2L → ties on wins,  HIGH NRR  (boundary wins)
        # Mumbai (2):     2W, 2L → ties on wins,  MID  NRR
        # Delhi (3):      2W, 2L → ties on wins,  LOW  NRR  (narrow wins, must be eliminated)
        # Chennai (4):    loses all — bottom
        plays = [
            (find(0, 1), 0, 1, 60, 12),     # Hyd beat Bng by 48
            (find(0, 2), 0, 2, 60, 12),     # Hyd beat Mum by 48
            (find(0, 3), 0, 3, 60, 12),     # Hyd beat Del by 48
            (find(0, 4), 0, 4, 60, 12),     # Hyd beat Che by 48
            (find(1, 2), 1, 2, 80, 8),      # Bng smash Mum by 72 — huge NRR boost
            (find(1, 4), 1, 4, 80, 8),      # Bng smash Che by 72 — huge NRR boost
            (find(1, 3), 3, 1, 50, 40),     # Del beat Bng by 10
            (find(2, 3), 2, 3, 60, 36),     # Mum beat Del by 24 — decent NRR boost
            (find(2, 4), 2, 4, 60, 36),     # Mum beat Che by 24 — decent NRR boost
            (find(3, 4), 3, 4, 30, 28),     # Del beat Che by 2 — barely
        ]

        for i, (mid, ba, bo, br, ow) in enumerate(plays):
            if not mid: continue
            print(f"   ▸ match {i+1}/{len(plays)} (id {mid})")
            await play_match(mid, ba, bo, br, ow)

        # Drain Redis cache so the UI fetch sees fresh NRR
        try:
            r = await tr.R
            await r.delete(f"cache:standings:{tid}")
        except Exception:
            pass
        await tr.R.delete(f"cache:standings:{tid}")

        # Final read
        st = (await c.get(f"/api/tournaments/{tid}/standings")).json().get("standings") or []
        print(f"\n→ FINAL STANDINGS for tournament id {tid}:")
        print(f"   {'Pos':>3} {'Team':<28} {'P':>3} {'W':>3} {'L':>3} {'Pts':>4} {'NRR':>8}")
        for i, s in enumerate(st):
            print(f"   {i+1:>3} {s['team_name']:<28} {s['played']:>3} {s['won']:>3} {s['lost']:>3} {s['points']:>4} {s['nrr']:>+8.3f}")
        print(f"\n→ Top 3 (qualified): {[s['team_name'] for s in st[:3]]}")
        print(f"→ Tournament title in UI: '{TOURNAMENT_NAME}'")
        print(f"→ Tournament id: {tid}\n")

    await tr.R.aclose()
    await tr.PG.close()


asyncio.run(main())
