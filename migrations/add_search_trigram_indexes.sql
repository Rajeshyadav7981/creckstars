-- Enable pg_trgm for fast ILIKE '%search%' queries (requires superuser)
-- Run as: psql -U postgres -d demo -f add_search_trigram_indexes.sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Tournament search: name + code
CREATE INDEX IF NOT EXISTS ix_tournaments_name_trgm ON tournaments USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_tournaments_code_trgm ON tournaments USING gin (tournament_code gin_trgm_ops);

-- Match search: code
CREATE INDEX IF NOT EXISTS ix_matches_code_trgm ON matches USING gin (match_code gin_trgm_ops);

-- Team search: name
CREATE INDEX IF NOT EXISTS ix_teams_name_trgm ON teams USING gin (name gin_trgm_ops);
