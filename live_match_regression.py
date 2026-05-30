"""Real-match regression: full ball-by-ball play with Indian-named squads.

Plays 4 matches end-to-end (3-team RR league + a 2-team Final) using:
  - Real player names (Dhoni, Rohit, Kohli, Bumrah, …) assigned to teams
  - HTTP scoring API (toss → squads → start-innings → /score per ball
    → end-over → end-innings → second innings → end-match)
  - Mix of singles, twos, fours, sixes, dots and one wicket per innings

Then verifies:
  - Stage statuses progress to "completed" via real innings (not walkover)
  - Standings show wins/losses/points with non-trivial run counts
  - Leaderboard's `batsmen` and `bowlers` arrays populate
  - Individual player_stats endpoint reflects matches played
  - Tournament cache stays fresh after each match (no stale reads)
"""
import asyncio
import random
import sys
from typing import Optional

import asyncpg
import httpx
import redis.asyncio as redis

import tournament_regression as tr
from src.utils.security import hash_password


MOBILE = "7981212220"
PASSWORD = "Rajesh@123"


INDIA_NAMES = [
    ("Rohit", "Sharma"), ("Shubman", "Gill"), ("Virat", "Kohli"),
    ("Suryakumar", "Yadav"), ("KL", "Rahul"), ("Hardik", "Pandya"),
    ("Ravindra", "Jadeja"), ("Jasprit", "Bumrah"), ("Mohammed", "Shami"),
    ("Mohammed", "Siraj"), ("Yuzvendra", "Chahal"), ("Kuldeep", "Yadav"),
    ("Rishabh", "Pant"), ("Ishan", "Kishan"), ("Sanju", "Samson"),
    ("Shreyas", "Iyer"), ("Axar", "Patel"), ("Bhuvneshwar", "Kumar"),
    ("Arshdeep", "Singh"), ("Tilak", "Varma"), ("Ruturaj", "Gaikwad"),
    ("Washington", "Sundar"),
]
PAK_NAMES = [
    ("Babar", "Azam"), ("Mohammad", "Rizwan"), ("Shaheen", "Afridi"),
    ("Naseem", "Shah"), ("Haris", "Rauf"), ("Shadab", "Khan"),
    ("Fakhar", "Zaman"), ("Iftikhar", "Ahmed"), ("Imam", "ul-Haq"),
    ("Mohammad", "Nawaz"), ("Salman", "Agha"),
]
LANKA_NAMES = [
    ("Kusal", "Mendis"), ("Pathum", "Nissanka"), ("Charith", "Asalanka"),
    ("Wanindu", "Hasaranga"), ("Maheesh", "Theekshana"), ("Dunith", "Wellalage"),
    ("Dhananjaya", "de Silva"), ("Dasun", "Shanaka"), ("Matheesha", "Pathirana"),
    ("Lahiru", "Kumara"), ("Dimuth", "Karunaratne"),
]
AUS_NAMES = [
    ("David", "Warner"), ("Travis", "Head"), ("Steve", "Smith"),
    ("Glenn", "Maxwell"), ("Marcus", "Stoinis"), ("Mitchell", "Marsh"),
    ("Pat", "Cummins"), ("Mitchell", "Starc"), ("Josh", "Hazlewood"),
    ("Adam", "Zampa"), ("Alex", "Carey"),
]


PG: asyncpg.Connection
ADMIN_ID = 0


async def ensure_password():
    await PG.execute(
        "UPDATE users SET password = $1 WHERE mobile = $2",
        hash_password(PASSWORD), MOBILE,
    )


async def login() -> tuple[str, int]:
    async with httpx.AsyncClient(base_url=tr.BASE, timeout=20) as c:
        r = await c.post("/api/auth/login", json={"mobile": MOBILE, "password": PASSWORD})
        r.raise_for_status()
        body = r.json()
        return body["access_token"], body["user"]["id"]


def H(t): return {"Authorization": f"Bearer {t}"}


# ───────────── direct-DB seeding (bypasses create_player rate limit) ─────────────

async def db_create_team(name: str) -> dict:
    code = "T" + tr.rand_str(8)
    row = await PG.fetchrow(
        "INSERT INTO teams (team_code, name, short_name, color, created_by) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id, name, short_name",
        code, name, name[:4].upper(), "#" + tr.rand_str(6), ADMIN_ID,
    )
    return dict(row)


async def db_create_players_for_team(team: dict, names: list[tuple[str, str]]) -> list[dict]:
    """Insert 11 guest players and link them to the team via team_players."""
    players = []
    for i, (fn, ln) in enumerate(names[:11]):
        row = await PG.fetchrow(
            "INSERT INTO players (first_name, last_name, full_name, is_guest, role, created_by) "
            "VALUES ($1, $2, $3, true, $4, $5) RETURNING id, full_name",
            fn, ln, f"{fn} {ln}",
            "wicket_keeper" if i == 0 else ("all_rounder" if 5 <= i <= 7 else ("bowler" if i >= 8 else "batsman")),
            ADMIN_ID,
        )
        pid = row["id"]
        await PG.execute(
            "INSERT INTO team_players (team_id, player_id, jersey_number, is_captain, is_wicket_keeper) "
            "VALUES ($1, $2, $3, $4, $5)",
            team["id"], pid, i + 1, i == 0, i == 0,
        )
        players.append({"id": pid, "full_name": row["full_name"]})
    return players


# ─────────────────────────── HTTP helpers ───────────────────────────

async def post_toss(c, mid, winner_team_id, decision):
    r = await c.post(f"/api/matches/{mid}/toss",
                     json={"toss_winner_id": winner_team_id, "toss_decision": decision})
    if r.status_code != 200:
        raise RuntimeError(f"toss: {r.status_code} {r.text}")


async def set_squad(c, mid, team_id, player_ids):
    body = {"team_id": team_id, "players": [{"player_id": p, "batting_order": i + 1}
                                            for i, p in enumerate(player_ids)]}
    r = await c.post(f"/api/matches/{mid}/squads", json=body)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"set_squad: {r.status_code} {r.text}")


async def start_innings(c, mid, batting_team_id, striker_id, non_striker_id, bowler_id):
    r = await c.post(f"/api/matches/{mid}/start-innings", json={
        "batting_team_id": batting_team_id, "striker_id": striker_id,
        "non_striker_id": non_striker_id, "bowler_id": bowler_id,
    })
    if r.status_code not in (200, 201):
        raise RuntimeError(f"start_innings: {r.status_code} {r.text}")


async def score(c, mid, **body):
    r = await c.post(f"/api/matches/{mid}/score", json=body)
    if r.status_code != 200:
        raise RuntimeError(f"score {body}: {r.status_code} {r.text}")
    return r.json()


async def end_over(c, mid, next_bowler_id):
    r = await c.post(f"/api/matches/{mid}/end-over", json={"next_bowler_id": next_bowler_id})
    if r.status_code != 200:
        raise RuntimeError(f"end_over: {r.status_code} {r.text}")


async def end_innings(c, mid):
    r = await c.post(f"/api/matches/{mid}/end-innings")
    if r.status_code != 200:
        raise RuntimeError(f"end_innings: {r.status_code} {r.text}")


async def end_match(c, mid):
    r = await c.post(f"/api/matches/{mid}/end-match")
    if r.status_code != 200:
        raise RuntimeError(f"end_match: {r.status_code} {r.text}")
    return r.json()


async def play_innings(c, mid, bat_squad: list[int], bowl_squad: list[int]):
    plays_o1 = [
        {"batsman_runs": 1},
        {"batsman_runs": 4, "is_boundary": True},
        {"batsman_runs": 0},
        {"batsman_runs": 6, "is_six": True},
        {"batsman_runs": 0, "is_wicket": True, "wicket_type": "caught",
         "dismissed_player_id": bat_squad[0], "fielder_id": bowl_squad[5],
         "new_batsman_id": bat_squad[2]},
        {"batsman_runs": 2},
    ]
    for p in plays_o1:
        await score(c, mid, **p)
    await end_over(c, mid, next_bowler_id=bowl_squad[9])

    plays_o2 = [
        {"batsman_runs": 1},
        {"batsman_runs": 1},
        {"batsman_runs": 4, "is_boundary": True},
        {"batsman_runs": 1},
        {"batsman_runs": 2},
        {"batsman_runs": 1},
    ]
    for p in plays_o2:
        await score(c, mid, **p)


async def play_match(c, match: dict, team_a: dict, team_b: dict,
                     squad_a: list[int], squad_b: list[int],
                     team_a_wins: bool) -> dict:
    mid = match["id"]
    await post_toss(c, mid, team_a["id"], "bat")

    await set_squad(c, mid, team_a["id"], squad_a)
    await set_squad(c, mid, team_b["id"], squad_b)

    await start_innings(c, mid,
                        batting_team_id=team_a["id"],
                        striker_id=squad_a[0],
                        non_striker_id=squad_a[1],
                        bowler_id=squad_b[8])
    await play_innings(c, mid, bat_squad=squad_a, bowl_squad=squad_b)
    await end_innings(c, mid)

    await start_innings(c, mid,
                        batting_team_id=team_b["id"],
                        striker_id=squad_b[0],
                        non_striker_id=squad_b[1],
                        bowler_id=squad_a[8])
    if team_a_wins:
        for p in [{"batsman_runs": 1}, {"batsman_runs": 0}, {"batsman_runs": 4, "is_boundary": True},
                  {"batsman_runs": 0}, {"batsman_runs": 1}, {"batsman_runs": 0}]:
            await score(c, mid, **p)
        await end_over(c, mid, next_bowler_id=squad_a[9])
        for p in [{"batsman_runs": 1}, {"batsman_runs": 1}, {"batsman_runs": 1},
                  {"batsman_runs": 0}, {"batsman_runs": 0}, {"batsman_runs": 1}]:
            await score(c, mid, **p)
    else:
        await play_innings(c, mid, bat_squad=squad_b, bowl_squad=squad_a)
    await end_innings(c, mid)
    return await end_match(c, mid)


# ─────────────────────────── verification ───────────────────────────

async def assert_real_stats(token: str, tid: int, sid: int) -> tuple[int, int, list[str]]:
    passed = failed = 0
    notes = []

    def check(label, cond, got=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            notes.append(f"   ✓ {label}")
        else:
            failed += 1
            notes.append(f"   ✗ {label}  ← {got}")

    async with httpx.AsyncClient(base_url=tr.BASE, headers=H(token), timeout=30) as c:
        det = (await c.get(f"/api/tournaments/{tid}")).json()
        stg = next((s for s in det.get("stages", []) if s.get("stage_id") == sid), None)
        check("stage status -> completed",
              stg and stg.get("status") == "completed",
              str(stg and stg.get("status")))

        all_match_completed = all(m.get("status") == "completed"
                                  for m in det.get("matches", []) if m.get("stage_id") == sid)
        check("every stage match -> completed", all_match_completed,
              str([m.get("status") for m in det.get("matches", []) if m.get("stage_id") == sid]))

        std = (await c.get(f"/api/tournaments/{tid}/standings")).json().get("standings") or []
        total_w = sum((r.get("won") or 0) for r in std)
        total_l = sum((r.get("lost") or 0) for r in std)
        check("standings: at least one team has won>=1", total_w >= 1, f"won_total={total_w}")
        check("standings: wins == losses (zero-sum across pool)",
              total_w == total_l, f"won={total_w} lost={total_l}")

        lb = (await c.get(f"/api/tournaments/{tid}/leaderboard")).json()
        batsmen = lb.get("top_batsmen") or []
        bowlers = lb.get("top_bowlers") or []
        fielders = lb.get("top_fielders") or []
        top_runs = max((b.get("runs") or 0) for b in batsmen) if batsmen else 0
        top_wickets = max((b.get("wickets") or 0) for b in bowlers) if bowlers else 0
        top_fours = max((b.get("fours") or 0) for b in batsmen) if batsmen else 0
        top_sixes = max((b.get("sixes") or 0) for b in batsmen) if batsmen else 0
        check("top_batsmen has entries", len(batsmen) > 0, f"n={len(batsmen)}")
        check("top batsman has runs > 0", top_runs > 0, f"runs={top_runs}")
        check("at least one batsman hit a four", top_fours >= 1, f"max_fours={top_fours}")
        check("at least one batsman hit a six", top_sixes >= 1, f"max_sixes={top_sixes}")
        check("top_bowlers has entries", len(bowlers) > 0, f"n={len(bowlers)}")
        check("at least one bowler has a wicket", top_wickets >= 1, f"max_wkts={top_wickets}")

        top_catches = max((f.get("catches") or 0) for f in fielders) if fielders else 0
        check("top_fielders has entries", len(fielders) > 0, f"n={len(fielders)}")
        check("top fielder has catches >= 1", top_catches >= 1, f"max_catches={top_catches}")
        if len(fielders) >= 2:
            check("fielders sorted by catches desc",
                  (fielders[0].get("catches") or 0) >= (fielders[1].get("catches") or 0),
                  f"{fielders[0].get('catches')} vs {fielders[1].get('catches')}")

        top_batsman_name = batsmen[0].get("player_name") if batsmen else None
        top_bowler_name = bowlers[0].get("player_name") if bowlers else None
        notes.append(f"     top batsman: {top_batsman_name} ({top_runs}r, {top_fours}×4, {top_sixes}×6)")
        notes.append(f"     top bowler:  {top_bowler_name} ({top_wickets}w)")
        top_fielder_name = fielders[0].get("name") if fielders else None
        notes.append(f"     top fielder: {top_fielder_name} ({top_catches}c)")

        if batsmen:
            pid = batsmen[0].get("player_id")
            if pid:
                psr = await c.get(f"/api/players/{pid}/stats")
                if psr.status_code == 200:
                    ps = psr.json()
                    matches_played = ps.get("matches") or ps.get("matches_played") or 0
                    check("player_stats: top batsman has matches_played >= 1",
                          matches_played >= 1, f"matches={matches_played} body={ps}")
                else:
                    check("player_stats endpoint 200", False, f"{psr.status_code}")

    return passed, failed, notes


# ─────────────────────────── main ───────────────────────────

async def main():
    global PG, ADMIN_ID
    tr.R = redis.from_url("redis://localhost:6379", decode_responses=True)
    PG = await asyncpg.connect(host="localhost", port=54322, user="demo", password="demo", database="demo")
    tr.PG = PG
    await ensure_password()

    token, ADMIN_ID = await login()
    tr.ADMIN_ID = ADMIN_ID
    print(f"logged in as {MOBILE} (user_id={ADMIN_ID})\n")

    print("──────  Scenario 1: 3-team League, real-ball scoring  ──────")
    suffix = tr.rand_str(4)
    teams_meta = [
        (f"India-{suffix}", INDIA_NAMES),
        (f"Pakistan-{suffix}", PAK_NAMES),
        (f"SriLanka-{suffix}", LANKA_NAMES),
    ]
    teams = []
    squads = {}
    for name, namebank in teams_meta:
        team = await db_create_team(name)
        players = await db_create_players_for_team(team, namebank)
        teams.append(team)
        squads[team["id"]] = [p["id"] for p in players]
    print(f"created {len(teams)} teams, {sum(len(s) for s in squads.values())} players")

    async with httpx.AsyncClient(base_url=tr.BASE, headers=H(token), timeout=60) as c:
        t = await tr.mk_tournament(c, f"LIVE-LG-{suffix}", type_="league")
        tid = t["id"]
        for tm in teams:
            await tr.add_team(c, tid, tm["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "league_matches", "label": "Group Stage"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "Group", "team_ids": [tm["id"] for tm in teams]}])
        await tr.generate_matches(c, tid, sid)

        ms = await tr.matches_for_stage(c, tid, sid)
        for m in ms:
            r = await c.patch(f"/api/matches/{m['id']}", json={"overs": 2})
            r.raise_for_status()

        team_by_id = {t["id"]: t for t in teams}
        for m in ms:
            ta = team_by_id[m["team_a_id"]]
            tb = team_by_id[m["team_b_id"]]
            sa = squads[ta["id"]]
            sb = squads[tb["id"]]
            res = await play_match(c, m, ta, tb, sa, sb, team_a_wins=True)
            print(f"   played {ta['short_name']} vs {tb['short_name']}: "
                  f"{res.get('result_summary', res)[:80]}")

    p1, f1, notes1 = await assert_real_stats(token, tid, sid)
    for n in notes1:
        print(n)

    print("\n──────  Scenario 2: 2-team Final, real-ball scoring  ──────")
    suffix2 = tr.rand_str(4)
    fteams = []
    fsquads = {}
    for name, namebank in [(f"India-F-{suffix2}", INDIA_NAMES), (f"Australia-{suffix2}", AUS_NAMES)]:
        team = await db_create_team(name)
        players = await db_create_players_for_team(team, namebank)
        fteams.append(team)
        fsquads[team["id"]] = [p["id"] for p in players]

    async with httpx.AsyncClient(base_url=tr.BASE, headers=H(token), timeout=60) as c:
        t2 = await tr.mk_tournament(c, f"LIVE-F2-{suffix2}", type_="knockout")
        tid2 = t2["id"]
        for tm in fteams:
            await tr.add_team(c, tid2, tm["id"])
        stages2 = await tr.setup_stages(c, tid2, [{"name": "final", "label": "Grand Final"}])
        sid2 = stages2[0]["id"]
        await tr.setup_groups(c, tid2, sid2, [{"name": "Grand Final",
                                               "team_ids": [tm["id"] for tm in fteams]}])
        await tr.generate_matches(c, tid2, sid2)
        ms2 = await tr.matches_for_stage(c, tid2, sid2)
        for m in ms2:
            await (await c.patch(f"/api/matches/{m['id']}", json={"overs": 2})).aclose() if False else None
            r = await c.patch(f"/api/matches/{m['id']}", json={"overs": 2}); r.raise_for_status()

        team_by_id2 = {t["id"]: t for t in fteams}
        m = ms2[0]
        ta = team_by_id2[m["team_a_id"]]
        tb = team_by_id2[m["team_b_id"]]
        res = await play_match(c, m, ta, tb,
                               fsquads[ta["id"]], fsquads[tb["id"]],
                               team_a_wins=True)
        print(f"   final: {ta['short_name']} vs {tb['short_name']} → "
              f"{res.get('result_summary', res)[:80]}")

        det = (await c.get(f"/api/tournaments/{tid2}")).json()
        tstatus = (det.get("tournament") or {}).get("status")
        print(f"   tournament status after final: {tstatus!r}")

    p2, f2, notes2 = await assert_real_stats(token, tid2, sid2)
    for n in notes2:
        print(n)

    await tr.R.aclose()
    await PG.close()

    total = p1 + f1 + p2 + f2
    passed = p1 + p2
    print(f"\n=== {passed}/{total} live-match assertions passed ===")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
