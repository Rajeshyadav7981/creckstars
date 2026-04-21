"""
Reproduce the 500 seen at over 29.1 of the 50-over load test.
Scores a long innings as fast as possible, prints the first failure's
full response body (now includes traceback thanks to the new global
exception handler).
"""
import asyncio
import httpx
import random
import sys
import time
from datetime import date

BASE = "http://localhost:7981"
ADMIN = {"mobile": "7981212220", "password": "Rajesh@123"}


async def main():
    random.seed(7)
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.post("/api/auth/login", json=ADMIN)
        r.raise_for_status()
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # Build two fresh teams
        ts = str(int(time.time()))[-6:]
        team_ids = []
        player_lists = []
        for side in ("R", "L"):
            tr = await c.post("/api/teams",
                              json={"name": f"Repro {side} {ts}", "short_name": f"{side}{ts[-2:]}", "color": "#444"},
                              headers=h)
            tr.raise_for_status()
            tid = tr.json()["id"]
            team_ids.append(tid)
            pids = []
            for i in range(13):
                pr = await c.post("/api/players", json={
                    "first_name": f"{side}{i+1}", "last_name": ts,
                    "role": ["batsman", "bowler", "all_rounder", "wicket_keeper"][i % 4],
                    "batting_style": "right_hand",
                    "bowling_style": "fast" if i < 6 else "spin",
                }, headers=h)
                pr.raise_for_status()
                pid = pr.json()["id"]
                pids.append(pid)
                await c.post(f"/api/teams/{tid}/players",
                             json={"player_id": pid, "jersey_number": i + 1},
                             headers=h)
            player_lists.append(pids)

        team_a, team_b = team_ids
        pa, pb = player_lists
        print(f"Teams: A={team_a} B={team_b}")

        # Create 50-over match
        mr = await c.post("/api/matches", json={
            "team_a_id": team_a, "team_b_id": team_b,
            "overs": 50, "match_type": "Group",
            "match_date": date.today().isoformat(),
        }, headers=h)
        mr.raise_for_status()
        mid = mr.json()["id"]
        print(f"Match: {mid}")

        # Squads
        for tid, pids in ((team_a, pa), (team_b, pb)):
            await c.post(f"/api/matches/{mid}/squads", json={
                "team_id": tid,
                "players": [{"player_id": p, "batting_order": i + 1} for i, p in enumerate(pids[:11])],
            }, headers=h)

        await c.post(f"/api/matches/{mid}/toss",
                     json={"toss_winner_id": team_a, "toss_decision": "bat"}, headers=h)

        await c.post(f"/api/matches/{mid}/start-innings", json={
            "batting_team_id": team_a,
            "striker_id": pa[0], "non_striker_id": pa[1],
            "bowler_id": pb[0],
        }, headers=h)

        # Score until first failure. Cycle 3 bowlers (0,1,2,0,1,2,...)
        bowlers = pb[:3]
        bowler_idx = 0
        batsmen = list(pa)
        striker_idx = 0
        next_bat_idx = 2

        n_balls = 0
        for over in range(50):
            legal = 0
            safety = 0
            while legal < 6 and safety < 30:
                safety += 1
                roll = random.random()
                if roll < 0.05:
                    payload = {"extra_type": "wide", "extra_runs": 0, "batsman_runs": 0}
                    delta = 0
                elif roll < 0.08:
                    payload = {"extra_type": "noball", "batsman_runs": 1, "extra_runs": 0}
                    delta = 0
                elif roll < 0.11:
                    payload = {"extra_type": "bye", "extra_runs": 1, "batsman_runs": 0}
                    delta = 1
                elif roll < 0.18 and next_bat_idx < 10:
                    payload = {
                        "batsman_runs": 0, "is_wicket": True, "wicket_type": "bowled",
                        "dismissed_player_id": batsmen[striker_idx],
                        "new_batsman_id": batsmen[next_bat_idx],
                    }
                    delta = 1
                    next_bat_idx += 1
                else:
                    payload = {"batsman_runs": random.choice([0, 1, 2, 4, 6]),
                               "is_boundary": False}
                    delta = 1

                r = await c.post(f"/api/matches/{mid}/score", json=payload, headers=h)
                n_balls += 1
                if r.status_code != 200:
                    print(f"\n⚠ FAILURE at ball {n_balls} (over {over}.{legal})")
                    print(f"Status: {r.status_code}")
                    print(f"Payload: {payload}")
                    try:
                        print(f"Response: {r.json()}")
                    except Exception:
                        print(f"Response: {r.text}")
                    # Also fetch live-state for context
                    ls = await c.get(f"/api/matches/{mid}/live-state")
                    print(f"Live state (public): {ls.json()}")
                    return
                legal += delta
                d = r.json()
                if d.get("innings_complete"):
                    print(f"\nInnings complete at ball {n_balls} (over {over}.{legal})")
                    return
            if over < 49:
                next_bowler = bowlers[(bowler_idx + 1) % 3]
                r = await c.post(f"/api/matches/{mid}/end-over",
                                 json={"next_bowler_id": next_bowler}, headers=h)
                if r.status_code != 200:
                    print(f"\n⚠ end-over FAILURE at over {over}")
                    print(f"Status: {r.status_code}")
                    try: print(f"Response: {r.json()}")
                    except: print(f"Response: {r.text}")
                    return
                bowler_idx = (bowler_idx + 1) % 3

        print(f"\nCompleted all {n_balls} balls without failure.")


if __name__ == "__main__":
    asyncio.run(main())
