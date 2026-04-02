"""Cricket rules validation engine — extracted from ScoringService."""


class CricketRules:
    """Validates cricket laws for deliveries."""

    @staticmethod
    def should_swap_strike(batsman_runs, extra_type, extra_runs):
        """Determine if striker/non-striker should swap after a delivery.
        Cricket law: swap if total runs scored off that delivery is odd.
        - Wide: total = 1 (penalty) + extra_runs (overthrows). Swap if odd.
        - No-ball: total = batsman_runs + extra_runs (penalty doesn't count for swap).
        - Bye/Legbye: total = extra_runs. Swap if odd.
        - Normal: total = batsman_runs. Swap if odd.
        """
        if extra_type == 'wide':
            # Wide penalty (1) + any overthrows
            return (1 + extra_runs) % 2 == 1
        if extra_type in ('bye', 'legbye'):
            return extra_runs % 2 == 1
        if extra_type == 'noball':
            # No-ball penalty doesn't affect strike. Only runs scored matter.
            return (batsman_runs + extra_runs) % 2 == 1
        return batsman_runs % 2 == 1

    @staticmethod
    def is_legal_delivery(extra_type):
        """Check if delivery counts as a legal ball."""
        return extra_type not in ('wide', 'noball')

    @staticmethod
    def calculate_total_runs(batsman_runs, extra_type, extra_runs):
        """Calculate total runs for a delivery including extras."""
        if extra_type == 'wide':
            return extra_runs + 1
        if extra_type == 'noball':
            return batsman_runs + extra_runs + 1
        return batsman_runs + extra_runs

    @staticmethod
    def validate_wicket_on_extra(extra_type, wicket_type, is_free_hit):
        """Validate that wicket type is allowed on the given delivery type."""
        if is_free_hit:
            if wicket_type and wicket_type != 'run_out':
                return False, "Only run out allowed on free hit"
        if extra_type == 'wide':
            if wicket_type and wicket_type not in ('stumped', 'run_out'):
                return False, "Only stumped or run out allowed on wide"
        if extra_type == 'noball':
            if wicket_type and wicket_type != 'run_out':
                return False, "Only run out allowed on no ball"
        return True, None

    @staticmethod
    def is_innings_complete(total_wickets, current_ball, overs, total_overs=None):
        """Check if innings should end."""
        if total_wickets >= 10:
            return True
        max_balls = (overs or 20) * 6
        current_balls = current_ball  # already incremented
        if total_overs is not None:
            over_part = int(total_overs)
            ball_part = round((total_overs % 1) * 10)
            current_balls = over_part * 6 + ball_part
        return current_balls >= max_balls

    @staticmethod
    def format_how_out(wicket_type, bowler_name, fielder_name, striker_name):
        """Generate how_out description string."""
        if not wicket_type:
            return "not out"
        if wicket_type == 'bowled':
            return f"b {bowler_name}"
        if wicket_type == 'caught':
            return f"c {fielder_name} b {bowler_name}" if fielder_name else f"c & b {bowler_name}"
        if wicket_type == 'lbw':
            return f"lbw b {bowler_name}"
        if wicket_type == 'run_out':
            return f"run out ({fielder_name})" if fielder_name else "run out"
        if wicket_type == 'stumped':
            return f"st {fielder_name} b {bowler_name}" if fielder_name else f"st b {bowler_name}"
        if wicket_type == 'hit_wicket':
            return f"hit wicket b {bowler_name}"
        return wicket_type
