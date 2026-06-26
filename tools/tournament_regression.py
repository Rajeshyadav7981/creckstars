"""End-to-end regression for the tournament flow.

Covers:
  - Knockout-only with 2 / 5 / 8 / 9 / 17 teams (final, QF w/ byes, QF, R16, prelim).
  - Multi-stage knockout cascade with auto-progression on match completion.
  - League-only round-robin → standings.
  - League + Knockout cascade with qualified-team carry-over.
  - Custom stage_label end-to-end (set on POST → returned on GET).

Bypasses the OTP gate by seeding the Redis flag directly. Bypasses
slowapi by spacing the create-team calls.
"""
import asyncio
import random
import string
import sys
import time
from typing import Optional

import asyncpg
import httpx
import redis.asyncio as redis

BASE = "http://localhost:7981"
PWD = "TestPass123!"
R: Optional[redis.Redis] = None
PG: Optional[asyncpg.Connection] = None
ADMIN_ID: int = 0


def rand_str(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def rand_mobile() -> str:
    return "9" + "".join(random.choices("0123456789", k=9))


async def seed_register_flag(mobile: str):
    await R.set(f"otp_verified:{mobile}:register", "1", ex=600)


async def admin_login_or_register() -> tuple[str, int]:
    mob = rand_mobile()
    await seed_register_flag(mob)
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as c:
        r = await c.post("/api/auth/register", json={
            "first_name": "Reg", "last_name": "Admin",
            "mobile": mob, "email": None, "password": PWD,
        })
        if r.status_code not in (200, 201):
            raise RuntimeError(f"register: {r.status_code} {r.text}")
        body = r.json()
        return body["access_token"], body["user"]["id"]


async def db_create_teams(n: int, label_prefix: str) -> list[dict]:
    """Bulk-insert teams via asyncpg so we sidestep the 20/min create_team rate limit."""
    teams = []
    for _ in range(n):
        code = "T" + rand_str(8)
        name = f"{label_prefix}-{rand_str(4)}"
        row = await PG.fetchrow(
            "INSERT INTO teams (team_code, name, short_name, color, created_by) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id, name",
            code, name, name[:4].upper(), "#FF5733", ADMIN_ID,
        )
        teams.append({"id": row["id"], "name": row["name"]})
    return teams


def H(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def mk_team(c: httpx.AsyncClient, name: str) -> dict:
    r = await c.post("/api/teams", json={
        "name": name, "short_name": name[:4].upper(), "color": f"#{rand_str(6)}",
    })
    r.raise_for_status()
    return r.json()


async def mk_tournament(c: httpx.AsyncClient, name: str, type_: str = "knockout") -> dict:
    r = await c.post("/api/tournaments", json={
        "name": name, "organizer_name": "Reg", "tournament_type": type_,
    })
    r.raise_for_status()
    return r.json()


async def add_team(c: httpx.AsyncClient, tid: int, team_id: int):
    r = await c.post(f"/api/tournaments/{tid}/teams", json={"team_id": team_id})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"add_team {team_id}: {r.status_code} {r.text}")


async def setup_stages(c: httpx.AsyncClient, tid: int, stages: list) -> list:
    r = await c.post(f"/api/tournaments/{tid}/stages", json={"stages": stages})
    r.raise_for_status()
    return r.json()["stages"]


async def setup_groups(c: httpx.AsyncClient, tid: int, sid: int, groups: list):
    r = await c.post(f"/api/tournaments/{tid}/stages/{sid}/groups", json={"groups": groups})
    r.raise_for_status()


async def generate_matches(c: httpx.AsyncClient, tid: int, sid: int):
    r = await c.post(f"/api/tournaments/{tid}/stages/{sid}/generate-matches")
    r.raise_for_status()


async def tournament_detail(c: httpx.AsyncClient, tid: int) -> dict:
    r = await c.get(f"/api/tournaments/{tid}")
    r.raise_for_status()
    return r.json()


async def matches_for_stage(c: httpx.AsyncClient, tid: int, sid: int) -> list:
    d = await tournament_detail(c, tid)
    return [m for m in d.get("matches", []) if m.get("stage_id") == sid]


async def complete_match_walkover(c: httpx.AsyncClient, tid: int, mid: int, winner_id: int):
    r = await c.post(f"/api/tournaments/{tid}/matches/{mid}/override", json={
        "winner_id": winner_id, "result_type": "walkover", "reason": "regression",
    })
    if r.status_code != 200:
        raise RuntimeError(f"override match {mid}: {r.status_code} {r.text}")


async def complete_all_in_stage(c: httpx.AsyncClient, tid: int, sid: int, prefer_first: bool = True) -> list:
    """Complete every match in a stage via walkover. Returns winner ids."""
    ms = await matches_for_stage(c, tid, sid)
    winners = []
    for m in ms:
        if m.get("status") == "completed":
            winners.append(m.get("winner_id"))
            continue
        winner = m["team_a_id"] if prefer_first else m["team_b_id"]
        await complete_match_walkover(c, tid, m["id"], winner)
        winners.append(winner)
    return winners


async def get_qualified_teams(c: httpx.AsyncClient, tid: int) -> list:
    r = await c.get(f"/api/tournaments/{tid}/qualified-teams")
    r.raise_for_status()
    return r.json()


def stage_label_from(stages_resp: list) -> Optional[str]:
    if stages_resp:
        return stages_resp[0].get("stage_label")
    return None


def stage_id_from(stages_resp: list) -> int:
    return stages_resp[0]["id"]


def find_stage(detail: dict, sid: int) -> Optional[dict]:
    for s in detail.get("stages", []):
        if s.get("stage_id") == sid:
            return s
    return None


# ──────────────────────── scenarios ────────────────────────

class Result:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.detail = ""

    def fail(self, msg: str):
        self.ok = False
        self.detail = msg


async def scenario_knockout_n(token: str, n: int, expected_round_name: str, expected_label: str,
                              custom_label: Optional[str] = None) -> Result:
    """Generic knockout-only with N teams. Optionally set a custom display label."""
    r = Result(f"knockout_only:{n}teams ({expected_round_name})" + (" +label" if custom_label else ""))
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=H(token), timeout=30) as c:
            t = await mk_tournament(c, f"KO{n}-{rand_str(4)}")
            tid = t["id"]
            teams = await db_create_teams(n, f"K{n}")
            for tm in teams:
                await add_team(c, tid, tm["id"])

            stages = await setup_stages(c, tid, [{
                "name": expected_round_name,
                "label": custom_label,
            }])
            sid = stage_id_from(stages)

            returned_label = stage_label_from(stages)
            if custom_label and returned_label != custom_label:
                return r if r.fail(f"label not stored: got {returned_label!r}, want {custom_label!r}") or True else r

            await setup_groups(c, tid, sid, [{
                "name": expected_label, "team_ids": [t["id"] for t in teams],
            }])
            await generate_matches(c, tid, sid)

            ms = await matches_for_stage(c, tid, sid)
            expected_matches = n // 2
            if len(ms) != expected_matches:
                r.fail(f"expected {expected_matches} matches for {n} teams, got {len(ms)}")
                return r

            # Verify stage_label round-trips on GET
            det = await tournament_detail(c, tid)
            stg = find_stage(det, sid)
            if custom_label and stg and stg.get("stage_label") != custom_label:
                r.fail(f"GET stage_label mismatch: {stg.get('stage_label')!r} vs {custom_label!r}")
                return r

            # Complete all matches; team_a wins each (deterministic seeding)
            await complete_all_in_stage(c, tid, sid)
            # Re-fetch and check stage completion
            det2 = await tournament_detail(c, tid)
            stg2 = find_stage(det2, sid)
            if not stg2:
                r.fail("stage missing after completion")
                return r
            if stg2.get("status") != "completed":
                r.fail(f"stage status after completion: {stg2.get('status')!r}")
                return r
    except Exception as e:
        r.fail(f"exception: {type(e).__name__}: {e}")
    return r


async def scenario_knockout_cascade_8(token: str) -> Result:
    """8 teams → QF → SF → Final, completing every match, per-stage add-next."""
    r = Result("knockout_cascade_8teams: QF→SF→Final")
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=H(token), timeout=60) as c:
            t = await mk_tournament(c, f"KOC8-{rand_str(4)}")
            tid = t["id"]
            teams = await db_create_teams(8, "C8")
            for tm in teams:
                await add_team(c, tid, tm["id"])

            for round_name, expected_matches in [
                ("quarter_final", 4),
                ("semi_final", 2),
                ("final", 1),
            ]:
                # qualified teams: first stage uses ALL added teams; later stages use winners from prev
                if round_name == "quarter_final":
                    team_ids = [tm["id"] for tm in teams]
                else:
                    qt = await get_qualified_teams(c, tid)
                    team_ids = [q["team_id"] if "team_id" in q else q["id"] for q in qt]
                if len(team_ids) < (expected_matches * 2):
                    r.fail(f"only {len(team_ids)} qualified for {round_name} (need {expected_matches * 2})")
                    return r

                stages = await setup_stages(c, tid, [{"name": round_name}])
                sid = stage_id_from(stages)
                await setup_groups(c, tid, sid, [{
                    "name": round_name.replace("_", " ").title(),
                    "team_ids": team_ids[: expected_matches * 2],
                }])
                await generate_matches(c, tid, sid)
                ms = await matches_for_stage(c, tid, sid)
                if len(ms) != expected_matches:
                    r.fail(f"{round_name}: expected {expected_matches} matches, got {len(ms)}")
                    return r
                await complete_all_in_stage(c, tid, sid)
    except Exception as e:
        r.fail(f"exception: {type(e).__name__}: {e}")
    return r


async def scenario_league_only_4(token: str) -> Result:
    """4 teams league-only round-robin (6 matches) → completed."""
    r = Result("league_only:4teams round-robin (6 matches)")
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=H(token), timeout=60) as c:
            t = await mk_tournament(c, f"LG4-{rand_str(4)}", type_="league")
            tid = t["id"]
            teams = await db_create_teams(4, "L4")
            for tm in teams:
                await add_team(c, tid, tm["id"])

            stages = await setup_stages(c, tid, [{
                "name": "pool_games",
                "label": "Pool Games",
                "qualification_rule": {"top_n": 4, "from": "each_group"},
            }])
            sid = stage_id_from(stages)
            await setup_groups(c, tid, sid, [{
                "name": "Pool", "team_ids": [tm["id"] for tm in teams],
            }])
            await generate_matches(c, tid, sid)
            ms = await matches_for_stage(c, tid, sid)
            if len(ms) != 6:
                r.fail(f"expected 6 round-robin matches, got {len(ms)}")
                return r
            await complete_all_in_stage(c, tid, sid)
            det = await tournament_detail(c, tid)
            stg = find_stage(det, sid)
            if stg and stg.get("stage_label") != "Pool Games":
                r.fail(f"label round-trip failed: {stg.get('stage_label')}")
    except Exception as e:
        r.fail(f"exception: {type(e).__name__}: {e}")
    return r


async def scenario_league_plus_knockout_4(token: str) -> Result:
    """4 teams: league (6 matches) → top 2 qualify → Final."""
    r = Result("league+knockout:4teams league → top2 → Final")
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=H(token), timeout=60) as c:
            t = await mk_tournament(c, f"LKO4-{rand_str(4)}", type_="league_knockout")
            tid = t["id"]
            teams = await db_create_teams(4, "LK4")
            for tm in teams:
                await add_team(c, tid, tm["id"])

            # League stage
            stages = await setup_stages(c, tid, [{
                "name": "league_matches",
                "label": "Round Robin",
                "qualification_rule": {"top_n": 2, "from": "each_group"},
            }])
            sid_l = stage_id_from(stages)
            await setup_groups(c, tid, sid_l, [{
                "name": "League", "team_ids": [tm["id"] for tm in teams],
            }])
            await generate_matches(c, tid, sid_l)
            ms = await matches_for_stage(c, tid, sid_l)
            if len(ms) != 6:
                r.fail(f"league: expected 6 matches, got {len(ms)}"); return r
            await complete_all_in_stage(c, tid, sid_l)

            # Final stage with top 2 qualifiers (custom label "Grand Finale")
            qt = await get_qualified_teams(c, tid)
            qtids = [q.get("team_id") or q.get("id") for q in qt][:2]
            if len(qtids) < 2:
                r.fail(f"only {len(qtids)} qualified for final"); return r
            stages_f = await setup_stages(c, tid, [{"name": "final", "label": "Grand Finale"}])
            sid_f = stage_id_from(stages_f)
            await setup_groups(c, tid, sid_f, [{"name": "Grand Finale", "team_ids": qtids}])
            await generate_matches(c, tid, sid_f)
            ms_f = await matches_for_stage(c, tid, sid_f)
            if len(ms_f) != 1:
                r.fail(f"final: expected 1 match, got {len(ms_f)}"); return r
            await complete_all_in_stage(c, tid, sid_f)

            det = await tournament_detail(c, tid)
            stg_f = find_stage(det, sid_f)
            if stg_f and stg_f.get("stage_label") != "Grand Finale":
                r.fail(f"final label round-trip: {stg_f.get('stage_label')}"); return r
    except Exception as e:
        r.fail(f"exception: {type(e).__name__}: {e}")
    return r


async def main():
    global R, PG, ADMIN_ID
    R = redis.from_url("redis://localhost:6379", decode_responses=True)
    PG = await asyncpg.connect(host="localhost", port=54322, user="demo", password="demo", database="demo")

    print("logging in admin…")
    token, ADMIN_ID = await admin_login_or_register()
    print(f"got token (len={len(token)}), admin user_id={ADMIN_ID}\n")

    scenarios = [
        scenario_knockout_n(token, 2, "final", "Final", custom_label="Grand Final"),
        scenario_knockout_n(token, 4, "semi_final", "Semi Final"),
        scenario_knockout_n(token, 5, "quarter_final", "Quarter Final"),
        scenario_knockout_n(token, 8, "quarter_final", "Quarter Final"),
        scenario_knockout_n(token, 9, "round_of_16", "Round of 16"),
        scenario_knockout_n(token, 17, "preliminary_round", "Preliminary Round"),
        scenario_knockout_cascade_8(token),
        scenario_league_only_4(token),
        scenario_league_plus_knockout_4(token),
    ]
    results = []
    for coro in scenarios:
        res = await coro
        results.append(res)
        flag = "PASS" if res.ok else "FAIL"
        print(f"[{flag}] {res.name}")
        if not res.ok:
            print(f"       → {res.detail}")

    await R.aclose()
    await PG.close()
    passed = sum(1 for r in results if r.ok)
    print(f"\n=== {passed}/{len(results)} passed ===")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
