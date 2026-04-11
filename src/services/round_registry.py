"""Round registry — single source of truth for every tournament round type.

Adding a new round (e.g. round_of_16, super_over_eliminator,
multi_group_league) means appending one entry to ROUND_CATALOG below
and, if the round needs a new pairing rule, registering one function
in PAIR_STRATEGIES. No other code in the backend needs to change —
generate_group_matches and on_match_completed both read from this registry.
"""
from dataclasses import dataclass
from typing import Optional, List
from itertools import combinations


@dataclass(frozen=True)
class RoundDef:
    name: str                # canonical stage_name (DB value)
    label: str               # human-readable label
    kind: str                # "league" | "knockout"
    min_teams: int           # min teams the round can run with
    max_teams: Optional[int] # cap (None = unbounded)
    pair_strategy: str       # key into PAIR_STRATEGIES


ROUND_CATALOG: List[RoundDef] = [
    RoundDef(
        name="league_matches",
        label="League Matches",
        kind="league",
        min_teams=2,
        max_teams=None,
        pair_strategy="round_robin",
    ),
    RoundDef(
        # Second round-robin played by qualified teams from the league phase.
        # CricHeroes calls this Super 4 / Super 6 / Super 8. Same mechanics as
        # league_matches, distinct stage_name so the UI and DB can tell them
        # apart.
        name="super_league",
        label="Super League",
        kind="league",
        min_teams=3,
        max_teams=None,
        pair_strategy="round_robin",
    ),
    RoundDef(
        name="round_of_16",
        label="Round of 16",
        kind="knockout",
        min_teams=16,
        max_teams=16,
        pair_strategy="cross_seed",
    ),
    RoundDef(
        name="quarter_final",
        label="Quarter Final",
        kind="knockout",
        min_teams=5,
        max_teams=8,
        pair_strategy="cross_seed",
    ),
    RoundDef(
        name="semi_final",
        label="Semi Final",
        kind="knockout",
        min_teams=3,
        max_teams=4,
        pair_strategy="cross_seed",
    ),
    RoundDef(
        name="final",
        label="Final",
        kind="knockout",
        min_teams=2,
        max_teams=2,
        pair_strategy="cross_seed",
    ),
]


def by_name(name: str) -> Optional[RoundDef]:
    for r in ROUND_CATALOG:
        if r.name == name:
            return r
    return None


def is_knockout(name: str) -> bool:
    r = by_name(name)
    return bool(r and r.kind == "knockout")


# ---------------------------------------------------------------------------
# Pair strategies
#
# A strategy takes a list of team ids (already in seed order) and returns
# a list of (team_a_id, team_b_id) pairs to create as matches.
# ---------------------------------------------------------------------------

def _round_robin(team_ids: List[int]) -> List[tuple]:
    """Every pair plays once (Circle Method via combinations)."""
    return list(combinations(team_ids, 2))


def _cross_seed(team_ids: List[int]) -> List[tuple]:
    """Cross-seed the bracket: 1v8, 2v7, 3v6, 4v5 (1vN, 2vN-1, ...)."""
    pairs = []
    for i in range(len(team_ids) // 2):
        pairs.append((team_ids[i], team_ids[len(team_ids) - 1 - i]))
    return pairs


PAIR_STRATEGIES = {
    "round_robin": _round_robin,
    "cross_seed": _cross_seed,
}


def pair_teams(strategy: str, team_ids: List[int]) -> List[tuple]:
    fn = PAIR_STRATEGIES.get(strategy)
    if not fn:
        raise ValueError(f"Unknown pair strategy: {strategy}")
    return fn(team_ids)
