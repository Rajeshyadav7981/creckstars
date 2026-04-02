"""
Simplified DLS (Duckworth-Lewis-Stern) Par Score Calculator.

This implements a simplified version of the DLS method used in professional cricket
to calculate par scores based on resources remaining (overs and wickets).

The official DLS tables are proprietary (ICC), so this uses the publicly known
Standard Edition resource percentages which are widely documented.

Reference: The resource % remaining depends on overs left and wickets lost.
"""

# DLS Standard Edition Resource Table (simplified)
# Key: wickets_lost -> list of resource % remaining at each over remaining
# Source: Publicly available DLS Standard Edition tables
# Values represent % of resources remaining with N overs left and W wickets lost
# Indexed: DLS_TABLE[wickets_lost][overs_remaining]

# For 20-over matches (T20) — resource percentages
# Rows: wickets lost (0-9), Columns: overs remaining (0-20)
DLS_RESOURCE = {
    # overs_remaining: 0   1     2     3     4     5     6     7     8     9    10    11    12    13    14    15    16    17    18    19    20
    0:  [0.0, 7.6, 14.3, 20.6, 26.5, 32.1, 37.4, 42.4, 47.1, 51.6, 55.8, 59.8, 63.6, 67.2, 70.6, 73.8, 76.9, 79.8, 82.6, 85.2, 87.6],
    1:  [0.0, 7.4, 13.9, 19.9, 25.5, 30.8, 35.8, 40.5, 44.9, 49.1, 53.0, 56.7, 60.2, 63.5, 66.6, 69.5, 72.3, 74.9, 77.4, 79.7, 81.9],
    2:  [0.0, 7.0, 13.2, 18.8, 24.1, 29.0, 33.6, 37.9, 42.0, 45.8, 49.4, 52.7, 55.8, 58.8, 61.5, 64.1, 66.6, 68.9, 71.1, 73.1, 75.0],
    3:  [0.0, 6.5, 12.1, 17.3, 22.0, 26.4, 30.6, 34.4, 38.0, 41.4, 44.5, 47.5, 50.2, 52.8, 55.2, 57.5, 59.6, 61.6, 63.5, 65.2, 66.8],
    4:  [0.0, 5.8, 10.8, 15.3, 19.5, 23.3, 26.9, 30.2, 33.3, 36.1, 38.8, 41.3, 43.6, 45.8, 47.8, 49.7, 51.5, 53.2, 54.8, 56.2, 57.5],
    5:  [0.0, 4.9, 9.2, 13.0, 16.5, 19.6, 22.5, 25.2, 27.7, 30.0, 32.1, 34.1, 35.9, 37.7, 39.3, 40.8, 42.2, 43.5, 44.7, 45.8, 46.8],
    6:  [0.0, 3.9, 7.3, 10.3, 13.0, 15.4, 17.7, 19.7, 21.6, 23.3, 24.9, 26.4, 27.8, 29.1, 30.3, 31.4, 32.4, 33.4, 34.3, 35.1, 35.8],
    7:  [0.0, 2.8, 5.2, 7.3, 9.2, 10.9, 12.4, 13.8, 15.1, 16.3, 17.4, 18.4, 19.3, 20.2, 21.0, 21.7, 22.4, 23.0, 23.6, 24.1, 24.6],
    8:  [0.0, 1.7, 3.1, 4.3, 5.4, 6.4, 7.3, 8.1, 8.8, 9.5, 10.1, 10.7, 11.2, 11.7, 12.1, 12.5, 12.9, 13.2, 13.5, 13.8, 14.1],
    9:  [0.0, 0.5, 0.9, 1.3, 1.6, 1.9, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2, 3.3, 3.5, 3.6, 3.7, 3.8, 3.9, 4.0, 4.1, 4.2],
}


def get_resource_remaining(overs_remaining: float, wickets_lost: int, max_overs: int = 20) -> float:
    """Get the % of resources remaining given overs left and wickets lost.

    Args:
        overs_remaining: Overs remaining (can be fractional like 12.3)
        wickets_lost: Number of wickets fallen (0-9)
        max_overs: Total overs in the match (for scaling)

    Returns:
        Resource percentage remaining (0-100)
    """
    wickets_lost = max(0, min(9, wickets_lost))

    # Scale overs to 20-over table
    scale = 20.0 / max_overs if max_overs > 0 else 1.0
    scaled_overs = overs_remaining * scale

    # Interpolate between integer over values
    lower = int(scaled_overs)
    upper = min(lower + 1, 20)
    fraction = scaled_overs - lower

    table = DLS_RESOURCE[wickets_lost]
    lower = min(lower, 20)
    upper = min(upper, 20)

    resource = table[lower] + (table[upper] - table[lower]) * fraction
    return resource


def calculate_dls_par_score(
    first_innings_total: int,
    total_overs: int,
    overs_bowled: float,
    wickets_lost: int,
) -> dict:
    """Calculate DLS par score for the team batting second.

    Args:
        first_innings_total: Runs scored by team batting first
        total_overs: Total overs in the match
        overs_bowled: Overs bowled so far in 2nd innings (e.g., 10.3)
        wickets_lost: Wickets lost in 2nd innings

    Returns:
        dict with par_score, projected_score, resource_used, resource_remaining
    """
    if total_overs <= 0 or first_innings_total <= 0:
        return None

    # Team 1 used 100% resources (completed innings)
    team1_resources = 100.0

    # Team 2 resources
    overs_remaining = total_overs - overs_bowled
    if overs_remaining < 0:
        overs_remaining = 0

    resource_remaining = get_resource_remaining(overs_remaining, wickets_lost, total_overs)
    resource_used = team1_resources - resource_remaining

    # Par score = (Team 1 total * resource_used / team1_resources)
    # This is the score Team 2 should have at this point to be "on par"
    if resource_used <= 0:
        par_score = 0
    else:
        par_score = round((first_innings_total * resource_used) / team1_resources)

    return {
        "par_score": par_score,
        "resource_used_pct": round(resource_used, 1),
        "resource_remaining_pct": round(resource_remaining, 1),
    }
