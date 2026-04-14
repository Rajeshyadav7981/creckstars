-- Performance indexes migration
-- Run this on your production database to add missing indexes

CREATE INDEX IF NOT EXISTS ix_innings_match_status ON innings (match_id, status);
CREATE INDEX IF NOT EXISTS ix_deliveries_innings_created ON deliveries (innings_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_batting_sc_innings_runs ON batting_scorecards (innings_id, runs DESC);
CREATE INDEX IF NOT EXISTS ix_bowling_sc_innings_wickets ON bowling_scorecards (innings_id, wickets DESC);
CREATE INDEX IF NOT EXISTS ix_match_squads_player_match ON match_squads (player_id, match_id);
CREATE INDEX IF NOT EXISTS ix_mentions_mentioner ON mentions (mentioner_user_id, created_at DESC);
