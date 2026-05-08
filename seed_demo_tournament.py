"""One-off seeder — builds a complete demo tournament you can open end-to-end
in the app.

Creates an organizer, 6 teams of 12 local-Indian-named players, generates a
round-robin league + semi-finals + final, and simulates ball-by-ball scoring
for every match so you see real totals, top batters, bowling figures, catches,
run-outs, and stumpings across the whole tournament.

Prereqs:
  - Backend running at http://localhost:7981 (or override with $API_URL)
  - Empty-ish DB (fresh mobile `9876500100` for the demo organizer)

Run:
  python seed_demo_tournament.py
"""
from __future__ import annotations

import os
import random
import sys
import time
from typing import Optional

import requests

API_URL = os.environ.get("API_URL", "http://localhost:7981")

# ── Demo content ─────────────────────────────────────────────────────────────

ORGANIZER = {
    "mobile": "9876500100",
    "email": "demo.organizer@creckstars.test",
    "password": "Demo@2026",
    "first_name": "Demo",
    "last_name": "Organizer",
    "username": f"demo_org_{random.randint(1000, 9999)}",
}

TOURNAMENT_NAME = "Mumbai Corporate Premier League 2026"
TOURNAMENT_CITY = "Mumbai"

TEAMS = [
    {"name": "Bandra Blasters",  "short": "BB",  "color": "#E53935", "city": "Mumbai"},
    {"name": "Andheri Avengers", "short": "AA",  "color": "#1E88E5", "city": "Mumbai"},
    {"name": "Thane Tigers",     "short": "TT",  "color": "#FB8C00", "city": "Thane"},
    {"name": "Navi Mumbai Nawabs","short":"NMN", "color": "#43A047", "city": "Navi Mumbai"},
    {"name": "Pune Panthers",    "short": "PP",  "color": "#8E24AA", "city": "Pune"},
    {"name": "Nashik Knights",   "short": "NK",  "color": "#FDD835", "city": "Nashik"},
]

# 12 players per team — realistic local Indian club-cricket names.
PLAYER_NAMES = [
    # Bandra Blasters
    ["Rohit Deshmukh", "Aarav Kulkarni", "Vihaan Shinde", "Kabir Patil",
     "Arjun Mhatre", "Reyansh Joshi", "Aditya Naik", "Ishaan Rane",
     "Dhruv Bhosale", "Vivaan Pawar", "Krish Sawant", "Rudra Gaikwad"],
    # Andheri Avengers
    ["Sairaj Iyer", "Omkar Nair", "Tanish Menon", "Parth Ranganathan",
     "Aditya Krishnan", "Aryan Subramaniam", "Rithvik Balaji", "Shaurya Pillai",
     "Aniket Venkatesh", "Yash Srinivasan", "Manav Rao", "Veer Chandrasekhar"],
    # Thane Tigers
    ["Harshad Kadam", "Sameer Jadhav", "Mihir Salunkhe", "Ayush Mane",
     "Pranav Wagh", "Shivam Chavan", "Advait More", "Siddhant Palkar",
     "Tanmay Ghag", "Nachiket Bhoir", "Jayesh Pardeshi", "Atharva Sonawane"],
    # Navi Mumbai Nawabs
    ["Arshad Qureshi", "Imran Shaikh", "Zaid Khan", "Rehan Ansari",
     "Faizan Pathan", "Sohail Memon", "Bilal Siddiqui", "Haroon Sayyed",
     "Adnan Mulla", "Tanveer Ali", "Zeeshan Farooqi", "Aamir Inamdar"],
    # Pune Panthers
    ["Suyog Kale", "Rohan Bhave", "Sarthak Gokhale", "Ojas Phadke",
     "Kartik Limaye", "Tushar Karpe", "Piyush Barve", "Chinmay Kelkar",
     "Gaurav Apte", "Ronit Khurana", "Ameya Nagarkar", "Vedant Sohoni"],
    # Nashik Knights
    ["Bharat Wadekar", "Nikhil Ghorpade", "Varad Divate", "Rushikesh Salve",
     "Saahas Lokhande", "Mayank Sarode", "Jatin Borawake", "Abhinav Tambe",
     "Devansh Ambekar", "Hardik Wankhede", "Prakhar Satoskar", "Aryaman Katkar"],
]

# Player roles — balanced team: 6 batsmen, 4 bowlers, 1 all-rounder, 1 wicketkeeper.
ROLES = (["batsman"] * 6 + ["bowler"] * 4 + ["all_rounder"] + ["wicket_keeper"])


# ── HTTP plumbing ────────────────────────────────────────────────────────────

class Api:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.token: Optional[str] = None

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def post(self, path: str, json=None):
        r = self.session.post(f"{self.base}{path}", json=json or {}, headers=self._headers(), timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}

    def get(self, path: str, params=None):
        r = self.session.get(f"{self.base}{path}", params=params or {}, headers=self._headers(), timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}

    def put(self, path: str, json=None):
        r = self.session.put(f"{self.base}{path}", json=json or {}, headers=self._headers(), timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"PUT {path} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}


# ── Organizer + auth ─────────────────────────────────────────────────────────

def bootstrap_user(api: Api) -> None:
    """Register or login the demo organizer and stash the token."""
    try:
        r = api.post("/api/auth/register", {
            "first_name": ORGANIZER["first_name"],
            "last_name": ORGANIZER["last_name"],
            "mobile": ORGANIZER["mobile"],
            "email": ORGANIZER["email"],
            "password": ORGANIZER["password"],
            "username": ORGANIZER["username"],
        })
        api.token = r["access_token"]
        print(f"  ✓ registered organizer {ORGANIZER['mobile']}")
    except RuntimeError as e:
        if "already registered" not in str(e):
            raise
        r = api.post("/api/auth/login", {"mobile": ORGANIZER["mobile"], "password": ORGANIZER["password"]})
        api.token = r["access_token"]
        print(f"  ✓ logged in existing organizer {ORGANIZER['mobile']}")


# ── Teams + players ──────────────────────────────────────────────────────────

def create_team(api: Api, team_def: dict) -> dict:
    return api.post("/api/teams", {
        "name": team_def["name"],
        "short_name": team_def["short"],
        "color": team_def["color"],
        "city": team_def["city"],
    })


def create_player(api: Api, full_name: str, role: str) -> dict:
    parts = full_name.split(" ", 1)
    first, last = parts[0], parts[1] if len(parts) > 1 else ""
    return api.post("/api/players", {
        "first_name": first,
        "last_name": last,
        "role": role,
        "city": TOURNAMENT_CITY,
    })


def add_player_to_team(api: Api, team_id: int, player_id: int, role: str) -> None:
    api.post(f"/api/teams/{team_id}/players", {"player_id": player_id, "role": role})


def build_teams(api: Api) -> list[dict]:
    """Returns list of {team_id, players:[{id, name, role}]} in TEAMS order."""
    out = []
    for idx, td in enumerate(TEAMS):
        team = create_team(api, td)
        team_id = team["id"]
        roster = []
        for pi, pname in enumerate(PLAYER_NAMES[idx]):
            role = ROLES[pi]
            p = create_player(api, pname, role)
            add_player_to_team(api, team_id, p["id"], role)
            roster.append({"id": p["id"], "name": pname, "role": role})
        out.append({"team_id": team_id, "name": td["name"], "short": td["short"], "players": roster})
        print(f"  ✓ {td['name']} ({td['short']}) — 12 players")
    return out


# ── Tournament + stages + matches ────────────────────────────────────────────

def create_tournament(api: Api) -> int:
    t = api.post("/api/tournaments", {
        "name": TOURNAMENT_NAME,
        "city": TOURNAMENT_CITY,
        "country": "India",
        "overs_per_match": 20,
        "match_type": "t20",
    })
    print(f"  ✓ tournament #{t['id']} — {TOURNAMENT_NAME}")
    return t["id"]


def add_teams_to_tournament(api: Api, tournament_id: int, team_ids: list[int]) -> None:
    for tid in team_ids:
        api.post(f"/api/tournaments/{tournament_id}/teams", {"team_id": tid})
    print(f"  ✓ added {len(team_ids)} teams to tournament")


def setup_stages(api: Api, tournament_id: int, team_ids: list[int]) -> list[dict]:
    """League (round-robin, one group), Semi-Finals, Final."""
    stages_spec = [
        {"stage_name": "league", "stage_order": 1, "qualification_rule": {"top_n": 4}},
        {"stage_name": "semi_final", "stage_order": 2},
        {"stage_name": "final", "stage_order": 3},
    ]
    res = api.post(f"/api/tournaments/{tournament_id}/stages", {"stages": stages_spec})
    stages = res.get("stages", res) if isinstance(res, dict) else res
    # Fetch the stages back so we have the stage_ids
    st_list = api.get(f"/api/tournaments/{tournament_id}/stages")
    stages = st_list if isinstance(st_list, list) else st_list.get("stages", [])
    print(f"  ✓ {len(stages)} stages created")

    # Setup one group in the league stage with all 6 teams
    league = next(s for s in stages if s["stage_name"] == "league")
    api.post(f"/api/tournaments/{tournament_id}/stages/{league['stage_id']}/groups", {
        "groups": [
            {"group_name": "Group A", "team_ids": team_ids},
        ],
    })
    print(f"  ✓ league group 'Group A' with {len(team_ids)} teams")

    return stages


def generate_matches(api: Api, tournament_id: int, stage_id: int) -> list[dict]:
    api.post(f"/api/tournaments/{tournament_id}/stages/{stage_id}/generate-matches")
    # Pull the match list filtered by stage
    res = api.get("/api/matches", params={"tournament_id": tournament_id, "stage_id": stage_id, "limit": 100})
    return res


# ── Ball-by-ball scoring simulator ───────────────────────────────────────────

# Weighted ball outcomes for a realistic T20 innings.
# Each tuple: (batsman_runs, extra_type, extra_runs, is_wicket, weight)
OUTCOMES = [
    (0, None, 0, False, 28),       # dot ball
    (1, None, 0, False, 26),       # single
    (2, None, 0, False, 9),        # two
    (3, None, 0, False, 2),        # three
    (4, None, 0, False, 10),       # four
    (6, None, 0, False, 4),        # six
    (0, "wide", 1, False, 4),      # wide
    (0, "noball", 1, False, 1),    # no-ball
    (0, "bye", 1, False, 1),       # bye
    (0, "legbye", 1, False, 1),    # leg bye
    (0, None, 0, True, 6),         # wicket
]
WICKET_TYPES_WEIGHTED = [
    ("bowled", 20),
    ("caught", 40),     # → creates fielding catch record
    ("lbw", 15),
    ("run_out", 15),    # → creates run-out fielding record
    ("stumped", 7),     # → stumping record
    ("hit_wicket", 3),
]


def weighted_pick(pairs):
    total = sum(w for _, w in pairs)
    r = random.uniform(0, total)
    acc = 0
    for item, w in pairs:
        acc += w
        if r <= acc:
            return item
    return pairs[-1][0]


def pick_outcome():
    total = sum(w for *_, w in OUTCOMES)
    r = random.uniform(0, total)
    acc = 0
    for o in OUTCOMES:
        acc += o[-1]
        if r <= acc:
            return o
    return OUTCOMES[-1]


def set_toss_and_squads(api: Api, match_id: int, batting_first: dict, bowling_first: dict) -> None:
    api.post(f"/api/matches/{match_id}/toss", {
        "toss_winner_id": batting_first["team_id"],
        "toss_decision": "bat",
    })
    for side in (batting_first, bowling_first):
        api.post(f"/api/matches/{match_id}/squads", {
            "team_id": side["team_id"],
            "players": [{"player_id": p["id"], "is_playing": True} for p in side["players"]],
        })


def simulate_innings(api: Api, match_id: int, batting: dict, bowling: dict, target: Optional[int]) -> int:
    """Play one innings; returns total runs scored. Uses the live scoring API."""
    batting_ids = [p["id"] for p in batting["players"]]
    bowling_ids = [p["id"] for p in bowling["players"]]
    # Bowlers rotation — give slight preference to declared bowlers/all-rounder
    bowlers_priority = (
        [p["id"] for p in bowling["players"] if p["role"] in ("bowler", "all_rounder")]
        + [p["id"] for p in bowling["players"] if p["role"] not in ("bowler", "all_rounder", "wicket_keeper")]
    )
    striker_idx, non_striker_idx = 0, 1
    bowler_idx = 0
    bowler_id = bowlers_priority[0]

    # Start innings
    api.post(f"/api/matches/{match_id}/start-innings", {
        "batting_team_id": batting["team_id"],
        "striker_id": batting_ids[striker_idx],
        "non_striker_id": batting_ids[non_striker_idx],
        "bowler_id": bowler_id,
    })

    total_runs = 0
    wickets_fallen = 0
    next_batsman = 2

    for over in range(20):
        legal_balls = 0
        while legal_balls < 6:
            outcome = pick_outcome()
            runs, extra_type, extra_runs, is_wicket, _ = outcome

            payload = {
                "batsman_runs": runs if not is_wicket else 0,
                "extra_type": extra_type,
                "extra_runs": extra_runs,
                "is_wicket": is_wicket,
                "is_boundary": runs == 4,
                "is_six": runs == 6,
            }
            if is_wicket:
                wtype = weighted_pick(WICKET_TYPES_WEIGHTED)
                payload["wicket_type"] = wtype
                payload["dismissed_player_id"] = batting_ids[striker_idx]
                # Catch / run-out / stumping require a fielder
                if wtype in ("caught", "run_out", "stumped"):
                    # Pick a random fielder — not the bowler for caught+bowled clarity
                    fielder_pool = [pid for pid in bowling_ids if pid != bowler_id]
                    payload["fielder_id"] = random.choice(fielder_pool)
                if next_batsman < len(batting_ids):
                    payload["new_batsman_id"] = batting_ids[next_batsman]

            try:
                resp = api.post(f"/api/matches/{match_id}/score", payload)
            except RuntimeError as e:
                # 400 on "all wickets fallen" / "all overs bowled" — means innings over
                if "Innings is complete" in str(e) or "all overs bowled" in str(e):
                    return total_runs
                # Rate limited — back off briefly
                if "429" in str(e):
                    time.sleep(1.5)
                    continue
                raise

            total_runs = resp.get("innings_runs", total_runs)
            is_legal = resp.get("is_legal", True)
            if is_legal:
                legal_balls += 1
            # Swap strike on odd runs
            if runs in (1, 3) and not is_wicket:
                striker_idx, non_striker_idx = non_striker_idx, striker_idx
            if is_wicket:
                wickets_fallen += 1
                # Place new batsman at striker end
                if next_batsman < len(batting_ids):
                    striker_idx = next_batsman
                    next_batsman += 1
                if wickets_fallen >= 10 or resp.get("innings_complete"):
                    return total_runs
            if target is not None and total_runs >= target:
                return total_runs
            if resp.get("innings_complete"):
                return total_runs

        # End of over: swap strike + rotate bowler
        striker_idx, non_striker_idx = non_striker_idx, striker_idx
        # Pick next bowler (different from current)
        candidates = [b for b in bowlers_priority if b != bowler_id]
        bowler_id = random.choice(candidates) if candidates else bowlers_priority[0]
        if over < 19:  # no need to start a 21st over
            try:
                api.post(f"/api/matches/{match_id}/end-over", {"next_bowler_id": bowler_id})
            except RuntimeError as e:
                if "Innings" in str(e) and "completed" in str(e):
                    return total_runs
                raise

    return total_runs


def play_match(api: Api, match: dict, teams_by_id: dict) -> None:
    match_id = match["id"]
    team_a = teams_by_id[match["team_a_id"]]
    team_b = teams_by_id[match["team_b_id"]]

    # Team batting first: randomize for variety
    if random.random() < 0.5:
        first, second = team_a, team_b
    else:
        first, second = team_b, team_a

    set_toss_and_squads(api, match_id, first, second)
    total1 = simulate_innings(api, match_id, first, second, target=None)
    # End innings explicitly (some paths may not auto-flip)
    try:
        api.post(f"/api/matches/{match_id}/end-innings", {})
    except RuntimeError:
        pass

    # Start 2nd innings with target = total1 + 1
    simulate_innings(api, match_id, second, first, target=total1 + 1)
    # Finalize
    try:
        api.post(f"/api/matches/{match_id}/end-match", {})
    except RuntimeError as e:
        # Force-tie path if the simulator ended in an exact tie
        if "tied" in str(e).lower():
            try:
                api.post(f"/api/matches/{match_id}/end-match?force_tie=true", {})
            except RuntimeError:
                pass


# ── Orchestrator ─────────────────────────────────────────────────────────────

def main():
    print(f"→ API: {API_URL}")
    api = Api(API_URL)

    print("\n[1/5] Organizer")
    bootstrap_user(api)

    print("\n[2/5] Teams + players (6 teams × 12 players = 72)")
    teams = build_teams(api)

    print("\n[3/5] Tournament")
    tid = create_tournament(api)
    add_teams_to_tournament(api, tid, [t["team_id"] for t in teams])
    stages = setup_stages(api, tid, [t["team_id"] for t in teams])

    print("\n[4/5] League-stage matches")
    league_stage = next(s for s in stages if s["stage_name"] == "league")
    league_matches = generate_matches(api, tid, league_stage["stage_id"])
    print(f"  ✓ generated {len(league_matches)} league matches")

    teams_by_id = {t["team_id"]: t for t in teams}

    print(f"\n[5/5] Simulating {len(league_matches)} league matches (this takes a few minutes)")
    for i, m in enumerate(league_matches, 1):
        ta = teams_by_id[m["team_a_id"]]["short"]
        tb = teams_by_id[m["team_b_id"]]["short"]
        print(f"  [{i:>2}/{len(league_matches)}] {ta} vs {tb} (match {m['id']}) …", end="", flush=True)
        t0 = time.time()
        try:
            play_match(api, m, teams_by_id)
            print(f" done ({time.time() - t0:.1f}s)")
        except Exception as e:
            print(f" FAIL: {e}")
            continue

    print("\n✅ Demo tournament ready.")
    print(f"   Tournament ID: {tid}")
    print(f"   Open it in the app: tournament/{tid}")
    print(f"   Organizer creds: mobile={ORGANIZER['mobile']}  password={ORGANIZER['password']}")
    print("\n   Knockout (semi-final/final) matches are not auto-simulated —")
    print("   trigger them from the app after the league stage auto-progresses,")
    print("   or run the bracket-swap/override flow to fill them in manually.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
