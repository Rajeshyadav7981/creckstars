"""
Seed example tournaments into the dev account so the user can see different
stage shapes side-by-side in the UI.

Creates four tournaments — each up to and including the league round, so the
user can browse them in MyTournaments and explore the structures:

  1. "Example · 4 Team Quick Final"     — Group → Final
  2. "Example · 6 Team SF Cup"           — Group → SF → Final
  3. "Example · 8 Team Knockout Cup"     — Group → QF → SF → Final
  4. "Example · 10 Team Super 4"         — Group → Super League → SF → Final

After running, log into the app with 7981212220 / Rajesh@123 and you'll see
all four in MyTournaments. Each one has its league stage created and fixtures
generated — you can score the matches and trigger Create Next Stage from the
TournamentDetail screen.

Account: 7981212220 / Rajesh@123
Run:     cd backend && venv/bin/python tests/seed_example_tournaments.py
"""
import asyncio
import time
from datetime import date

import httpx

BASE = "http://localhost:7981"
ADMIN = {"mobile": "7981212220", "password": "Rajesh@123"}


def log(msg, ok=True):
    print(f"  [{'OK  ' if ok else 'FAIL'}] {msg}")


def err(r):
    try:
        return f"{r.status_code} {r.json().get('detail', r.text[:160])}"
    except Exception:
        return f"{r.status_code} {r.text[:160]}"


async def build_team(c, h, name, short, color, ts):
    r = await c.post(
        "/api/teams",
        json={"name": name, "short_name": short, "color": color},
        headers=h,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Create team {name}: {err(r)}")
    team = r.json()
    # Add 11 minimal players so the team is playable
    for i in range(11):
        pr = await c.post(
            "/api/players",
            json={
                "first_name": f"{short}{i + 1}",
                "last_name": ts,
                "role": "batsman" if i < 4 else ("bowler" if i < 8 else "all_rounder"),
                "batting_style": "right_hand",
                "bowling_style": "fast" if i < 5 else "spin",
            },
            headers=h,
        )
        if pr.status_code == 200:
            await c.post(
                f"/api/teams/{team['id']}/players",
                json={"player_id": pr.json()["id"], "jersey_number": i + 1},
                headers=h,
            )
    return team


async def create_tournament(c, h, name, location):
    r = await c.post(
        "/api/tournaments",
        json={
            "name": name,
            "tournament_type": "league_knockout",
            "overs_per_match": 5,
            "ball_type": "tennis",
            "organizer_name": "Demo Organizer",
            "location": location,
            "start_date": date.today().isoformat(),
            "end_date": date.today().isoformat(),
            "entry_fee": 0,
            "prize_pool": 0,
        },
        headers=h,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Create tournament: {err(r)}")
    return r.json()


async def add_teams(c, h, tid, teams):
    for t in teams:
        await c.post(
            f"/api/tournaments/{tid}/teams",
            json={"team_id": t["id"]},
            headers=h,
        )


def split_groups(teams, teams_per_group):
    """Strict-fill distribution — same algorithm as the frontend wizard."""
    groups = []
    for i in range(0, len(teams), teams_per_group):
        groups.append(teams[i : i + teams_per_group])
    return groups


async def create_league_round(
    c, h, tid, teams, *, teams_per_group, top_n_per_group, round_label="League Matches"
):
    # 1. Create the league stage with qualification rule
    r = await c.post(
        f"/api/tournaments/{tid}/stages",
        json={
            "stages": [
                {
                    "name": "league_matches",
                    "qualification_rule": {"top_n": top_n_per_group, "from": "each_group"},
                }
            ]
        },
        headers=h,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Create league stage: {err(r)}")
    stages = r.json().get("stages", [])
    stage_id = stages[0]["id"]

    # 2. Build groups payload
    groups = split_groups(teams, teams_per_group)
    groups_payload = [
        {
            "name": round_label if len(groups) == 1 else f"Group {chr(65 + i)}",
            "team_ids": [t["id"] for t in g],
        }
        for i, g in enumerate(groups)
    ]
    r = await c.post(
        f"/api/tournaments/{tid}/stages/{stage_id}/groups",
        json={"groups": groups_payload},
        headers=h,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Setup groups: {err(r)}")

    # 3. Generate round-robin fixtures
    r = await c.post(
        f"/api/tournaments/{tid}/stages/{stage_id}/generate-matches",
        headers=h,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Generate matches: {err(r)}")
    return stage_id, r.json().get("matches_created", 0)


async def main():
    print("\n" + "=" * 70)
    print("  SEED EXAMPLE TOURNAMENTS — different stage shapes")
    print("=" * 70)

    async with httpx.AsyncClient(base_url=BASE, timeout=120) as c:
        # Reach + login
        r = await c.get("/health")
        log("Backend reachable", r.status_code == 200)

        r = await c.post("/api/auth/login", json=ADMIN)
        if r.status_code != 200:
            log("Login", False)
            print(err(r))
            return 1
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        log("Login")

        ts = str(int(time.time()))[-5:]
        # Build a shared pool of 10 teams with friendly names
        print("\n── Building 10 example teams ──")
        team_meta = [
            (f"Mumbai Strikers {ts}",  "MUM", "#1E88E5"),
            (f"Chennai Kings {ts}",    "CHE", "#FBC02D"),
            (f"Bangalore Blasters {ts}", "BLR", "#E53935"),
            (f"Delhi Dynamos {ts}",    "DEL", "#3949AB"),
            (f"Kolkata Knights {ts}",  "KOL", "#5E35B1"),
            (f"Punjab Lions {ts}",     "PUN", "#D81B60"),
            (f"Hyderabad Hawks {ts}",  "HYD", "#FB8C00"),
            (f"Rajasthan Royals {ts}", "RAJ", "#EC407A"),
            (f"Gujarat Giants {ts}",   "GUJ", "#00897B"),
            (f"Lucknow Lancers {ts}",  "LUC", "#43A047"),
        ]
        all_teams = []
        for name, short, color in team_meta:
            t = await build_team(c, h, name, short, color, ts)
            all_teams.append(t)
        log(f"Built {len(all_teams)} teams")

        # ── Tournament 1: 4 teams → Group → Final ─────────────────────────
        print("\n── Tournament 1: 4 Team Quick Final (Group → Final) ──")
        t1 = await create_tournament(
            c, h,
            f"Example · 4 Team Quick Final {ts}",
            "Marine Drive Ground",
        )
        await add_teams(c, h, t1["id"], all_teams[:4])
        sid, mc = await create_league_round(
            c, h, t1["id"], all_teams[:4],
            teams_per_group=4, top_n_per_group=2, round_label="League",
        )
        log(f"Tournament 1 ready — id={t1['id']}, stage={sid}, league matches={mc}")
        log("  Path: League [4] (6 matches) → top 2 → Final (1 match)")

        # ── Tournament 2: 6 teams → Group → SF → Final ────────────────────
        print("\n── Tournament 2: 6 Team SF Cup (Group → SF → Final) ──")
        t2 = await create_tournament(
            c, h,
            f"Example · 6 Team SF Cup {ts}",
            "Wankhede Stadium",
        )
        await add_teams(c, h, t2["id"], all_teams[:6])
        sid, mc = await create_league_round(
            c, h, t2["id"], all_teams[:6],
            teams_per_group=3, top_n_per_group=2, round_label="Pool",
        )
        log(f"Tournament 2 ready — id={t2['id']}, stage={sid}, league matches={mc}")
        log("  Path: League [3,3] (6 matches) → top 4 → SF (2) → Final (1)")

        # ── Tournament 3: 8 teams → Group → QF → SF → Final ───────────────
        print("\n── Tournament 3: 8 Team Knockout Cup (Group → QF → SF → Final) ──")
        t3 = await create_tournament(
            c, h,
            f"Example · 8 Team Knockout Cup {ts}",
            "Eden Gardens",
        )
        await add_teams(c, h, t3["id"], all_teams[:8])
        sid, mc = await create_league_round(
            c, h, t3["id"], all_teams[:8],
            teams_per_group=4, top_n_per_group=4, round_label="Group",
        )
        log(f"Tournament 3 ready — id={t3['id']}, stage={sid}, league matches={mc}")
        log("  Path: League [4,4] (12 matches) → top 8 → QF (4) → SF (2) → Final (1)")

        # ── Tournament 4: 10 teams → Group → Super League → SF → Final ────
        print("\n── Tournament 4: 10 Team Super 4 (Group → Super League → SF → Final) ──")
        t4 = await create_tournament(
            c, h,
            f"Example · 10 Team Super 4 {ts}",
            "Chinnaswamy Stadium",
        )
        await add_teams(c, h, t4["id"], all_teams[:10])
        sid, mc = await create_league_round(
            c, h, t4["id"], all_teams[:10],
            teams_per_group=5, top_n_per_group=2, round_label="Group",
        )
        log(f"Tournament 4 ready — id={t4['id']}, stage={sid}, league matches={mc}")
        log("  Path: League [5,5] (20 matches) → top 4 → Super League [4] (6) → top 2 → Final (1)")

        print("\n" + "=" * 70)
        print("  Done! Open the app and log in with 7981212220 / Rajesh@123")
        print("  Browse 'My Tournaments' to see all four example shapes.")
        print("=" * 70)
        for label, t in [
            ("4 Team",  t1),
            ("6 Team",  t2),
            ("8 Team",  t3),
            ("10 Team", t4),
        ]:
            print(f"    {label:8s} → id={t['id']}  code={t.get('tournament_code')}")
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
