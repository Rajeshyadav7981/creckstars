-- Covering indexes for player stats aggregations.
-- Current indexes are (innings_id, player_id) which don't help WHERE player_id = X.
-- These player_id-leading covering indexes allow index-only scans for the
-- batting/bowling aggregate queries (no heap fetch), ~3-10x faster.

CREATE INDEX IF NOT EXISTS ix_batting_sc_player_cover
  ON batting_scorecards(player_id)
  INCLUDE (runs, balls_faced, fours, sixes, is_out, innings_id);

CREATE INDEX IF NOT EXISTS ix_bowling_sc_player_cover
  ON bowling_scorecards(player_id)
  INCLUDE (overs_bowled, runs_conceded, wickets, maidens, wides, no_balls, dot_balls, economy_rate, innings_id);

-- Speeds up the DISTINCT ON (match_id) ORDER BY bs.id DESC in recent innings queries
CREATE INDEX IF NOT EXISTS ix_batting_sc_player_id_desc
  ON batting_scorecards(player_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_bowling_sc_player_id_desc
  ON bowling_scorecards(player_id, id DESC);
