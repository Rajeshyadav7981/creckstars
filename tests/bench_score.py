"""Micro-benchmark: measure POST /score latency after the refactor."""
import asyncio, httpx, random, time, statistics
from datetime import date

BASE = "http://localhost:7981"
ADMIN = {"mobile": "7981212220", "password": "Rajesh@123"}


async def main():
    random.seed(11)
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.post("/api/auth/login", json=ADMIN)
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        ts = str(int(time.time()))[-6:]
        teams = []
        players = []
        for side in ("X", "Y"):
            t = (await c.post("/api/teams",
                              json={"name": f"Bench {side} {ts}", "short_name": f"{side}{ts[-2:]}", "color": "#555"},
                              headers=h)).json()
            teams.append(t["id"])
            pids = []
            for i in range(13):
                pid = (await c.post("/api/players", json={
                    "first_name": f"{side}{i+1}", "last_name": ts,
                    "role": "batsman", "batting_style": "right_hand", "bowling_style": "fast",
                }, headers=h)).json()["id"]
                pids.append(pid)
                await c.post(f"/api/teams/{t['id']}/players",
                             json={"player_id": pid, "jersey_number": i + 1}, headers=h)
            players.append(pids)

        mid = (await c.post("/api/matches", json={
            "team_a_id": teams[0], "team_b_id": teams[1], "overs": 50,
            "match_type": "Group", "match_date": date.today().isoformat(),
        }, headers=h)).json()["id"]

        for tid, pids in zip(teams, players):
            await c.post(f"/api/matches/{mid}/squads", json={
                "team_id": tid,
                "players": [{"player_id": p, "batting_order": i + 1} for i, p in enumerate(pids[:11])],
            }, headers=h)
        await c.post(f"/api/matches/{mid}/toss",
                     json={"toss_winner_id": teams[0], "toss_decision": "bat"}, headers=h)
        await c.post(f"/api/matches/{mid}/start-innings", json={
            "batting_team_id": teams[0],
            "striker_id": players[0][0], "non_striker_id": players[0][1],
            "bowler_id": players[1][0],
        }, headers=h)

        # Bench: 60 scoring calls, plain singles
        latencies = []
        for i in range(60):
            t0 = time.perf_counter()
            r = await c.post(f"/api/matches/{mid}/score", json={"batsman_runs": 1}, headers=h)
            dt = (time.perf_counter() - t0) * 1000
            if r.status_code != 200:
                print(f"  ball {i}: FAIL {r.status_code} {r.text[:200]}")
                break
            latencies.append(dt)
            d = r.json()
            if d.get("over_complete") and not d.get("innings_complete"):
                nb = players[1][(i // 6 + 1) % 3]
                await c.post(f"/api/matches/{mid}/end-over", json={"next_bowler_id": nb}, headers=h)

        if latencies:
            latencies.sort()
            print(f"\n  POST /score benchmarks ({len(latencies)} calls):")
            print(f"    mean : {statistics.mean(latencies):6.1f} ms")
            print(f"    p50  : {latencies[len(latencies)//2]:6.1f} ms")
            print(f"    p95  : {latencies[int(len(latencies)*0.95)]:6.1f} ms")
            print(f"    p99  : {latencies[int(len(latencies)*0.99)]:6.1f} ms")
            print(f"    max  : {max(latencies):6.1f} ms")
            print(f"    min  : {min(latencies):6.1f} ms")


if __name__ == "__main__":
    asyncio.run(main())
