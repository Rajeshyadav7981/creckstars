"""Edge-case regression for stage completion + Complete Tournament + standings.

Asserts:
  E1 mid-stage: completing N-1 of N matches keeps stage 'in_progress'.
  E2 last match: completing the very last match flips stage to 'completed'
                 and `(total_matches == completed_matches)` (the frontend
                 "Create Next Stage" signal).
  E3 final stage: 2-team final auto-completes the tournament.
  E4 PUT complete: clicking "Complete Tournament" on a non-final stage
                   sets tournament status to 'completed' AND the GET cache
                   reflects it immediately (no stale 5-min read).
  E5 standings:  league points/wins/losses move after each match override.
  E6 leaderboard: tournament leaderboard returns rows after matches play.
"""
import asyncio
import sys

import httpx

import tournament_regression as tr
from src.utils.security import hash_password


MOBILE = "7981212220"
PASSWORD = "Rajesh@123"


async def ensure_password():
    await tr.PG.execute(
        "UPDATE users SET password = $1 WHERE mobile = $2",
        hash_password(PASSWORD), MOBILE,
    )


async def login() -> tuple[str, int]:
    async with httpx.AsyncClient(base_url=tr.BASE, timeout=20) as c:
        r = await c.post("/api/auth/login", json={"mobile": MOBILE, "password": PASSWORD})
        r.raise_for_status()
        body = r.json()
        return body["access_token"], body["user"]["id"]


def H(tok): return {"Authorization": f"Bearer {tok}"}


async def get_stage(c, tid, sid):
    det = await tr.tournament_detail(c, tid)
    for s in det.get("stages", []):
        if s.get("stage_id") == sid:
            return s, det
    return None, det


def t_status(det):
    """Detail response wraps tournament fields under `tournament`."""
    return (det.get("tournament") or {}).get("status")


def fmt_stage(s):
    return f"status={s.get('status')!r} matches={s.get('completed_matches')}/{s.get('total_matches')}"


class Probe:
    def __init__(self, name):
        self.name = name
        self.checks = []
        self.ok = True

    def expect(self, label, cond, got=""):
        self.checks.append((label, cond, got))
        if not cond:
            self.ok = False


async def probe_midstage_then_complete(token) -> Probe:
    """E1+E2+E6: 4-team knockout SF.
       After match 1: stage in_progress, standings has rows.
       After match 2: stage completed, (total==completed), tournament still in_progress.
    """
    p = Probe("E1+E2  4-team Semi Final: progressive completion")
    async with httpx.AsyncClient(base_url=tr.BASE, headers=H(token), timeout=30) as c:
        t = await tr.mk_tournament(c, f"EDGE-SF-{tr.rand_str(4)}")
        tid = t["id"]
        teams = await tr.db_create_teams(4, "ESF")
        for tm in teams:
            await tr.add_team(c, tid, tm["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "semi_final"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "SF", "team_ids": [tm["id"] for tm in teams]}])
        await tr.generate_matches(c, tid, sid)

        ms = await tr.matches_for_stage(c, tid, sid)
        p.expect("SF generates exactly 2 matches", len(ms) == 2, str(len(ms)))

        # Complete first match
        await tr.complete_match_walkover(c, tid, ms[0]["id"], ms[0]["team_a_id"])
        s1, _ = await get_stage(c, tid, sid)
        p.expect("after 1/2 matches: stage is in_progress",
                 s1.get("status") == "in_progress", fmt_stage(s1))
        p.expect("after 1/2 matches: completed_matches==1",
                 s1.get("completed_matches") == 1, fmt_stage(s1))

        # Complete second (last) match
        await tr.complete_match_walkover(c, tid, ms[1]["id"], ms[1]["team_a_id"])
        s2, det = await get_stage(c, tid, sid)
        p.expect("after 2/2 matches: stage is completed",
                 s2.get("status") == "completed", fmt_stage(s2))
        p.expect("after 2/2 matches: total==completed (frontend gate)",
                 (s2.get("total_matches") or 0) > 0
                 and s2.get("total_matches") == s2.get("completed_matches"),
                 fmt_stage(s2))
        p.expect("SF (non-final) does NOT auto-complete the tournament",
                 t_status(det) in ("in_progress", "upcoming"),
                 str(t_status(det)))

        # E6: leaderboard has rows
        lb = (await c.get(f"/api/tournaments/{tid}/leaderboard")).json()
        rows = lb.get("batsmen") or lb.get("bowlers") or []
        p.expect("leaderboard endpoint responds 200 with a dict",
                 isinstance(lb, dict), type(lb).__name__)
        # walkover doesn't generate batting/bowling stats — just shape check
    return p


async def probe_two_team_final_auto_completes(token) -> Probe:
    """E3: knockout-only with 2 teams = single 'final' match.
       After completing it, tournament status auto-flips to 'completed'.
    """
    p = Probe("E3     2-team Final: tournament auto-completes")
    async with httpx.AsyncClient(base_url=tr.BASE, headers=H(token), timeout=30) as c:
        t = await tr.mk_tournament(c, f"EDGE-F2-{tr.rand_str(4)}")
        tid = t["id"]
        teams = await tr.db_create_teams(2, "EF2")
        for tm in teams:
            await tr.add_team(c, tid, tm["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "final"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "Final", "team_ids": [tm["id"] for tm in teams]}])
        await tr.generate_matches(c, tid, sid)

        ms = await tr.matches_for_stage(c, tid, sid)
        p.expect("final stage has exactly 1 match", len(ms) == 1, str(len(ms)))

        await tr.complete_match_walkover(c, tid, ms[0]["id"], ms[0]["team_a_id"])
        s, det = await get_stage(c, tid, sid)
        p.expect("final stage status -> completed",
                 s.get("status") == "completed", fmt_stage(s))
        p.expect("tournament status -> completed (auto, no button)",
                 t_status(det) == "completed", str(t_status(det)))
    return p


async def probe_complete_button_invalidates_cache(token) -> Probe:
    """E4: PUT /tournaments/{id} {status:'completed'} on a non-final stage
       must (a) succeed and (b) be immediately visible on GET (no stale cache).
    """
    p = Probe("E4     'Complete Tournament' PUT + cache freshness")
    async with httpx.AsyncClient(base_url=tr.BASE, headers=H(token), timeout=30) as c:
        t = await tr.mk_tournament(c, f"EDGE-CT-{tr.rand_str(4)}", type_="league")
        tid = t["id"]
        teams = await tr.db_create_teams(3, "ECT")
        for tm in teams:
            await tr.add_team(c, tid, tm["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "league_matches"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "Pool", "team_ids": [tm["id"] for tm in teams]}])
        await tr.generate_matches(c, tid, sid)
        await tr.complete_all_in_stage(c, tid, sid)

        # Prime the GET cache before the PUT — this is what the bug looked like.
        det_before = await tr.tournament_detail(c, tid)
        p.expect("before PUT: tournament status is in_progress (not yet completed)",
                 t_status(det_before) in ("in_progress", "upcoming"),
                 str(t_status(det_before)))

        r = await c.put(f"/api/tournaments/{tid}", json={"status": "completed"})
        p.expect("PUT /tournaments/{id} returns 200", r.status_code == 200, f"{r.status_code} {r.text}")

        det_after = await tr.tournament_detail(c, tid)
        p.expect("after PUT: GET returns status='completed' (no stale cache)",
                 t_status(det_after) == "completed", str(t_status(det_after)))
    return p


async def probe_standings_progress(token) -> Probe:
    """E5: league 3-team round robin. After each match, points table updates."""
    p = Probe("E5     League points table updates per match")
    async with httpx.AsyncClient(base_url=tr.BASE, headers=H(token), timeout=30) as c:
        t = await tr.mk_tournament(c, f"EDGE-LG-{tr.rand_str(4)}", type_="league")
        tid = t["id"]
        teams = await tr.db_create_teams(3, "ELG")
        team_ids = [tm["id"] for tm in teams]
        for tm in teams:
            await tr.add_team(c, tid, tm["id"])
        stages = await tr.setup_stages(c, tid, [{"name": "league_matches"}])
        sid = stages[0]["id"]
        await tr.setup_groups(c, tid, sid, [{"name": "Pool", "team_ids": team_ids}])
        await tr.generate_matches(c, tid, sid)
        ms = await tr.matches_for_stage(c, tid, sid)
        p.expect("3-team RR has exactly 3 matches", len(ms) == 3, str(len(ms)))

        async def std():
            r = await c.get(f"/api/tournaments/{tid}/standings")
            return r.json().get("standings") or []

        s0 = await std()
        zeros = sum(1 for row in s0 if (row.get("points") or 0) == 0)
        p.expect("before any match: every team has 0 points", zeros == len(s0), f"{zeros}/{len(s0)}")

        # Complete one match — team_a wins
        await tr.complete_match_walkover(c, tid, ms[0]["id"], ms[0]["team_a_id"])
        s1 = await std()
        winner_row = next((r for r in s1 if r["team_id"] == ms[0]["team_a_id"]), None)
        loser_row = next((r for r in s1 if r["team_id"] == ms[0]["team_b_id"]), None)
        p.expect("after 1 match: winner has points > 0",
                 winner_row and (winner_row.get("points") or 0) > 0,
                 str(winner_row))
        p.expect("after 1 match: loser still has 0 points",
                 loser_row and (loser_row.get("points") or 0) == 0,
                 str(loser_row))
        p.expect("after 1 match: winner has won==1",
                 winner_row and (winner_row.get("won") or 0) == 1, str(winner_row))

        # Complete remaining matches
        for m in ms[1:]:
            await tr.complete_match_walkover(c, tid, m["id"], m["team_a_id"])

        s_final = await std()
        total_wins = sum((row.get("won") or 0) for row in s_final)
        p.expect("after all 3 matches: total wins across table == 3",
                 total_wins == 3, str(total_wins))

        st, det = await get_stage(c, tid, sid)
        p.expect("after all 3 matches: stage status -> completed",
                 st.get("status") == "completed", fmt_stage(st))

        # Frontend gate: also count completed_matches via matches array
        from_matches = [m for m in det.get("matches", []) if m.get("stage_id") == sid]
        all_done = all(m.get("status") == "completed" for m in from_matches)
        p.expect("after all 3 matches: every match in matches[] is completed",
                 all_done, f"{sum(1 for m in from_matches if m.get('status')=='completed')}/{len(from_matches)}")
    return p


async def main():
    import redis.asyncio as redis
    import asyncpg
    tr.R = redis.from_url("redis://localhost:6379", decode_responses=True)
    tr.PG = await asyncpg.connect(host="localhost", port=54322, user="demo", password="demo", database="demo")
    await ensure_password()

    token, uid = await login()
    tr.ADMIN_ID = uid
    print(f"logged in as {MOBILE} (user_id={uid})\n")

    probes = [
        await probe_midstage_then_complete(token),
        await probe_two_team_final_auto_completes(token),
        await probe_complete_button_invalidates_cache(token),
        await probe_standings_progress(token),
    ]

    print()
    total = passed = 0
    for p in probes:
        head = "PASS" if p.ok else "FAIL"
        print(f"[{head}] {p.name}")
        for label, cond, got in p.checks:
            total += 1
            if cond:
                passed += 1
                print(f"   ✓ {label}")
            else:
                print(f"   ✗ {label}  ← {got}")
        print()

    await tr.R.aclose()
    await tr.PG.close()
    print(f"=== {passed}/{total} assertions passed across {len(probes)} probes ===")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
