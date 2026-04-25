-- ══════════════════════════════════════════════════════════════════════
-- CrecKStars — Complete Database Schema
-- Version: 2.0 (2026-04-17)
--
-- Usage:
--   1. Create a fresh PostgreSQL database
--   2. Run as superuser (for pg_trgm extension):
--        psql -U postgres -d creckstars -f schema.sql
--   3. All tables, indexes, constraints, and extensions in one file
--   4. Safe to re-run (uses IF NOT EXISTS everywhere)
--
-- Tables: 27 | Indexes: 60+ | Extensions: pg_trgm
-- ══════════════════════════════════════════════════════════════════════

-- ── Extensions (requires superuser) ──
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ═══════════════════════════════════════
-- CORE: Users & Authentication
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    full_name VARCHAR(200) NOT NULL,
    mobile VARCHAR(15) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    password TEXT NOT NULL,
    username VARCHAR(30) UNIQUE,
    profile TEXT,                          -- photo URL or local path
    -- Cricket profile (optional)
    bio TEXT,
    city VARCHAR(100),
    state_province VARCHAR(100),
    country VARCHAR(100),
    date_of_birth DATE,
    batting_style VARCHAR(20),            -- right_hand | left_hand
    bowling_style VARCHAR(30),            -- right_arm_fast | left_arm_spin | etc
    player_role VARCHAR(20),              -- batsman | bowler | all_rounder | wicket_keeper
    -- Social
    followers_count INTEGER DEFAULT 0,
    following_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_users_mobile ON users (mobile);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username_lower ON users (LOWER(username));
CREATE INDEX IF NOT EXISTS ix_users_username_prefix ON users (username varchar_pattern_ops) WHERE username IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_users_full_name_lower ON users (LOWER(full_name) varchar_pattern_ops);
-- Trigram search (fast ILIKE '%query%')
CREATE INDEX IF NOT EXISTS ix_users_username_trgm ON users USING gin (username gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_users_full_name_trgm ON users USING gin (full_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS user_follows (
    follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    following_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (follower_id, following_id)
);
CREATE INDEX IF NOT EXISTS ix_follows_following ON user_follows (following_id);
CREATE INDEX IF NOT EXISTS ix_follows_follower ON user_follows (follower_id);
-- Composite indexes that cover (filter, sort) for keyset pagination on the
-- followers/following lists. A plain (following_id) index forces a sort step
-- per request — at millions of follows that's a spill to disk.
CREATE INDEX IF NOT EXISTS ix_follows_following_created ON user_follows (following_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_follows_follower_created ON user_follows (follower_id, created_at DESC);

CREATE TABLE IF NOT EXISTS otps (
    id SERIAL PRIMARY KEY,
    mobile VARCHAR(15) NOT NULL,
    otp_code VARCHAR(6) NOT NULL,
    is_verified BOOLEAN DEFAULT FALSE,
    purpose VARCHAR(20) NOT NULL,         -- register | login | reset_password
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_otps_mobile ON otps (mobile);
CREATE INDEX IF NOT EXISTS ix_otp_mobile_purpose_verified ON otps (mobile, purpose, is_verified, expires_at DESC);


-- ═══════════════════════════════════════
-- CRICKET: Players, Teams, Venues
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS venues (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    city VARCHAR(100),
    ground_type VARCHAR(50),
    address VARCHAR(500),
    latitude FLOAT,
    longitude FLOAT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_venues_geo ON venues (latitude, longitude) WHERE latitude IS NOT NULL;

CREATE TABLE IF NOT EXISTS players (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100),
    full_name VARCHAR(200) NOT NULL,
    mobile VARCHAR(15),
    -- Deliberate unlinkable stub (kid / walk-in / no phone). Never auto-links.
    is_guest BOOLEAN NOT NULL DEFAULT FALSE,
    date_of_birth DATE,
    bio TEXT,
    city VARCHAR(100),
    state_province VARCHAR(100),
    country VARCHAR(100),
    batting_style VARCHAR(20),
    bowling_style VARCHAR(30),
    role VARCHAR(20),
    profile_image TEXT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_players_created_by ON players (created_by);
CREATE INDEX IF NOT EXISTS ix_players_user_id ON players (user_id);
-- Fast lookup for the auto-link path (WHERE mobile = :m AND user_id IS NULL).
CREATE INDEX IF NOT EXISTS ix_players_mobile_stub ON players (mobile) WHERE user_id IS NULL;
-- Backfill column on existing DBs that predate this commit.
ALTER TABLE players ADD COLUMN IF NOT EXISTS is_guest BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    team_code VARCHAR(10) UNIQUE,
    name VARCHAR(200) NOT NULL,
    short_name VARCHAR(10),
    logo_url VARCHAR(500),
    color VARCHAR(7),
    home_ground VARCHAR(200),
    city VARCHAR(100),
    latitude FLOAT,
    longitude FLOAT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_teams_code ON teams (team_code) WHERE team_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_teams_name_trgm ON teams USING gin (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS team_players (
    id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    jersey_number INTEGER,
    is_captain BOOLEAN DEFAULT FALSE,
    is_vice_captain BOOLEAN DEFAULT FALSE,
    is_wicket_keeper BOOLEAN DEFAULT FALSE,
    UNIQUE(team_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_team_players_team ON team_players (team_id);
CREATE INDEX IF NOT EXISTS ix_team_players_player ON team_players (player_id);
CREATE INDEX IF NOT EXISTS ix_team_players_composite ON team_players (team_id, player_id);


-- ═══════════════════════════════════════
-- TOURNAMENTS
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS tournaments (
    id SERIAL PRIMARY KEY,
    tournament_code VARCHAR(10) UNIQUE,
    name VARCHAR(200) NOT NULL,
    tournament_type VARCHAR(20) NOT NULL DEFAULT 'league',
    overs_per_match INTEGER NOT NULL DEFAULT 20,
    ball_type VARCHAR(20) DEFAULT 'tennis',
    start_date DATE,
    end_date DATE,
    status VARCHAR(20) NOT NULL DEFAULT 'upcoming',
    organizer_name VARCHAR(200),
    location VARCHAR(500),
    entry_fee FLOAT DEFAULT 0,
    prize_pool FLOAT DEFAULT 0,
    banner_url VARCHAR(500),
    venue_id INTEGER REFERENCES venues(id),
    points_per_win INTEGER NOT NULL DEFAULT 2,
    points_per_draw INTEGER NOT NULL DEFAULT 1,
    points_per_no_result INTEGER NOT NULL DEFAULT 0,
    has_third_place_playoff BOOLEAN NOT NULL DEFAULT FALSE,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_tournaments_tournament_code ON tournaments (tournament_code);
CREATE INDEX IF NOT EXISTS ix_tournaments_created_by ON tournaments (created_by);
CREATE INDEX IF NOT EXISTS ix_tournaments_name_trgm ON tournaments USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_tournaments_code_trgm ON tournaments USING gin (tournament_code gin_trgm_ops);

CREATE TABLE IF NOT EXISTS tournament_teams (
    id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    UNIQUE(tournament_id, team_id)
);

CREATE TABLE IF NOT EXISTS tournament_stages (
    id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    stage_name VARCHAR(50) NOT NULL,
    stage_order INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'upcoming',
    qualification_rule JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tournament_id, stage_order)
);

CREATE TABLE IF NOT EXISTS tournament_groups (
    id SERIAL PRIMARY KEY,
    stage_id INTEGER NOT NULL REFERENCES tournament_stages(id) ON DELETE CASCADE,
    group_name VARCHAR(50) NOT NULL,
    group_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(stage_id, group_name)
);
CREATE INDEX IF NOT EXISTS ix_tournament_groups_stage ON tournament_groups (stage_id);

CREATE TABLE IF NOT EXISTS tournament_group_teams (
    id SERIAL PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES tournament_groups(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    qualification_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    UNIQUE(group_id, team_id)
);
CREATE INDEX IF NOT EXISTS ix_tournament_group_teams_group ON tournament_group_teams (group_id);


-- ═══════════════════════════════════════
-- MATCHES & SCORING
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS matches (
    id SERIAL PRIMARY KEY,
    match_code VARCHAR(10) UNIQUE,
    tournament_id INTEGER REFERENCES tournaments(id),
    team_a_id INTEGER NOT NULL REFERENCES teams(id),
    team_b_id INTEGER NOT NULL REFERENCES teams(id),
    venue_id INTEGER REFERENCES venues(id),
    match_date DATE,
    overs INTEGER NOT NULL DEFAULT 20,
    status VARCHAR(20) NOT NULL DEFAULT 'upcoming',
    toss_winner_id INTEGER REFERENCES teams(id),
    toss_decision VARCHAR(10),
    winner_id INTEGER REFERENCES teams(id),
    result_summary TEXT,
    result_type VARCHAR(20),
    current_innings INTEGER DEFAULT 0,
    match_type VARCHAR(20) DEFAULT 'group',
    time_slot VARCHAR(50),
    scorer_user_id INTEGER REFERENCES users(id),
    created_by INTEGER NOT NULL REFERENCES users(id),
    stage_id INTEGER REFERENCES tournament_stages(id),
    group_id INTEGER REFERENCES tournament_groups(id),
    match_number INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_matches_match_code ON matches (match_code);
CREATE INDEX IF NOT EXISTS ix_matches_tournament ON matches (tournament_id);
CREATE INDEX IF NOT EXISTS ix_matches_stage ON matches (stage_id);
CREATE INDEX IF NOT EXISTS ix_matches_created_by ON matches (created_by);
CREATE INDEX IF NOT EXISTS ix_matches_status ON matches (status);
CREATE INDEX IF NOT EXISTS ix_matches_status_created ON matches (status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_matches_created_by_status ON matches (created_by, status);
CREATE INDEX IF NOT EXISTS ix_matches_tournament_status ON matches (tournament_id, status);
CREATE INDEX IF NOT EXISTS ix_matches_team_ids ON matches (team_a_id, team_b_id);
CREATE INDEX IF NOT EXISTS ix_matches_code_trgm ON matches USING gin (match_code gin_trgm_ops);

CREATE TABLE IF NOT EXISTS match_squads (
    id SERIAL PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    is_playing BOOLEAN DEFAULT TRUE,
    batting_order INTEGER,
    UNIQUE(match_id, team_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_match_squads_match_team ON match_squads (match_id, team_id);
CREATE INDEX IF NOT EXISTS ix_match_squads_player ON match_squads (player_id);
CREATE INDEX IF NOT EXISTS ix_match_squads_player_match ON match_squads (player_id, match_id);

CREATE TABLE IF NOT EXISTS innings (
    id SERIAL PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    innings_number INTEGER NOT NULL,
    batting_team_id INTEGER NOT NULL REFERENCES teams(id),
    bowling_team_id INTEGER NOT NULL REFERENCES teams(id),
    total_runs INTEGER DEFAULT 0,
    total_wickets INTEGER DEFAULT 0,
    total_overs FLOAT DEFAULT 0.0,
    total_extras INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'not_started',
    target INTEGER,
    current_over INTEGER DEFAULT 0,
    current_ball INTEGER DEFAULT 0,
    current_striker_id INTEGER REFERENCES players(id),
    current_non_striker_id INTEGER REFERENCES players(id),
    current_bowler_id INTEGER REFERENCES players(id),
    is_free_hit BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(match_id, innings_number)
);
CREATE INDEX IF NOT EXISTS ix_innings_match_number ON innings (match_id, innings_number);
CREATE INDEX IF NOT EXISTS ix_innings_match_status ON innings (match_id, status);

CREATE TABLE IF NOT EXISTS deliveries (
    id SERIAL PRIMARY KEY,
    innings_id INTEGER NOT NULL REFERENCES innings(id),
    over_number INTEGER NOT NULL,
    ball_number INTEGER NOT NULL,
    actual_ball_seq INTEGER NOT NULL,
    striker_id INTEGER NOT NULL REFERENCES players(id),
    non_striker_id INTEGER NOT NULL REFERENCES players(id),
    bowler_id INTEGER NOT NULL REFERENCES players(id),
    batsman_runs INTEGER DEFAULT 0,
    is_boundary BOOLEAN DEFAULT FALSE,
    is_six BOOLEAN DEFAULT FALSE,
    extra_type VARCHAR(10),
    extra_runs INTEGER DEFAULT 0,
    total_runs INTEGER DEFAULT 0,
    is_wicket BOOLEAN DEFAULT FALSE,
    wicket_type VARCHAR(20),
    dismissed_player_id INTEGER REFERENCES players(id),
    fielder_id INTEGER REFERENCES players(id),
    is_legal BOOLEAN DEFAULT TRUE,
    commentary TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_deliveries_innings ON deliveries (innings_id);
CREATE INDEX IF NOT EXISTS ix_deliveries_innings_over_ball ON deliveries (innings_id, over_number, ball_number);
CREATE INDEX IF NOT EXISTS ix_deliveries_innings_seq ON deliveries (innings_id, actual_ball_seq DESC);
CREATE INDEX IF NOT EXISTS ix_deliveries_innings_created ON deliveries (innings_id, created_at DESC);
-- Guard against duplicate deliveries per innings under concurrent scoring.
CREATE UNIQUE INDEX IF NOT EXISTS ux_deliveries_innings_seq ON deliveries (innings_id, actual_ball_seq);

CREATE TABLE IF NOT EXISTS overs (
    id SERIAL PRIMARY KEY,
    innings_id INTEGER NOT NULL REFERENCES innings(id),
    over_number INTEGER NOT NULL,
    bowler_id INTEGER NOT NULL REFERENCES players(id),
    runs_conceded INTEGER DEFAULT 0,
    wickets INTEGER DEFAULT 0,
    wides INTEGER DEFAULT 0,
    no_balls INTEGER DEFAULT 0,
    is_maiden BOOLEAN DEFAULT FALSE,
    UNIQUE(innings_id, over_number)
);
CREATE INDEX IF NOT EXISTS ix_overs_innings_number ON overs (innings_id, over_number);

CREATE TABLE IF NOT EXISTS batting_scorecards (
    id SERIAL PRIMARY KEY,
    innings_id INTEGER NOT NULL REFERENCES innings(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    batting_position INTEGER,
    runs INTEGER DEFAULT 0,
    balls_faced INTEGER DEFAULT 0,
    fours INTEGER DEFAULT 0,
    sixes INTEGER DEFAULT 0,
    strike_rate FLOAT DEFAULT 0.0,
    how_out VARCHAR(100),
    is_out BOOLEAN DEFAULT FALSE,
    bowler_id INTEGER REFERENCES players(id),
    fielder_id INTEGER REFERENCES players(id),
    UNIQUE(innings_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_batting_sc_innings_player ON batting_scorecards (innings_id, player_id);
CREATE INDEX IF NOT EXISTS ix_batting_sc_innings_runs ON batting_scorecards (innings_id, runs DESC);
-- Covering index for player stats aggregation (index-only scan)
CREATE INDEX IF NOT EXISTS ix_batting_sc_player_cover ON batting_scorecards (player_id) INCLUDE (runs, balls_faced, fours, sixes, is_out, innings_id);
CREATE INDEX IF NOT EXISTS ix_batting_sc_player_id_desc ON batting_scorecards (player_id, id DESC);

CREATE TABLE IF NOT EXISTS bowling_scorecards (
    id SERIAL PRIMARY KEY,
    innings_id INTEGER NOT NULL REFERENCES innings(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    overs_bowled FLOAT DEFAULT 0.0,
    maidens INTEGER DEFAULT 0,
    runs_conceded INTEGER DEFAULT 0,
    wickets INTEGER DEFAULT 0,
    economy_rate FLOAT DEFAULT 0.0,
    wides INTEGER DEFAULT 0,
    no_balls INTEGER DEFAULT 0,
    dot_balls INTEGER DEFAULT 0,
    UNIQUE(innings_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_bowling_sc_innings_player ON bowling_scorecards (innings_id, player_id);
CREATE INDEX IF NOT EXISTS ix_bowling_sc_innings_wickets ON bowling_scorecards (innings_id, wickets DESC);
-- Covering index for player stats aggregation (index-only scan)
CREATE INDEX IF NOT EXISTS ix_bowling_sc_player_cover ON bowling_scorecards (player_id) INCLUDE (overs_bowled, runs_conceded, wickets, maidens, wides, no_balls, dot_balls, economy_rate, innings_id);
CREATE INDEX IF NOT EXISTS ix_bowling_sc_player_id_desc ON bowling_scorecards (player_id, id DESC);

CREATE TABLE IF NOT EXISTS fall_of_wickets (
    id SERIAL PRIMARY KEY,
    innings_id INTEGER NOT NULL REFERENCES innings(id),
    wicket_number INTEGER NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(id),
    runs_at_fall INTEGER NOT NULL,
    overs_at_fall FLOAT NOT NULL,
    delivery_id INTEGER REFERENCES deliveries(id)
);
CREATE INDEX IF NOT EXISTS ix_fall_of_wickets_innings ON fall_of_wickets (innings_id);

CREATE TABLE IF NOT EXISTS partnerships (
    id SERIAL PRIMARY KEY,
    innings_id INTEGER NOT NULL REFERENCES innings(id),
    wicket_number INTEGER NOT NULL,
    player_a_id INTEGER NOT NULL REFERENCES players(id),
    player_b_id INTEGER NOT NULL REFERENCES players(id),
    total_runs INTEGER DEFAULT 0,
    total_balls INTEGER DEFAULT 0,
    player_a_runs INTEGER DEFAULT 0,
    player_b_runs INTEGER DEFAULT 0,
    extras INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS ix_partnerships_innings ON partnerships (innings_id);

CREATE TABLE IF NOT EXISTS match_events (
    id SERIAL PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    event_type VARCHAR(20) NOT NULL,
    event_data JSONB,
    match_state JSONB,
    sequence_number INTEGER NOT NULL,
    is_undone BOOLEAN DEFAULT FALSE,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_match_events_match_seq ON match_events (match_id, sequence_number);
CREATE INDEX IF NOT EXISTS ix_match_events_match_seq_desc ON match_events (match_id, sequence_number DESC);


-- ═══════════════════════════════════════
-- COMMUNITY: Posts, Comments, Polls
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    text TEXT NOT NULL,
    title VARCHAR(300),
    tag VARCHAR(50),
    image_url TEXT,
    likes_count INTEGER DEFAULT 0,
    comments_count INTEGER DEFAULT 0,
    shares_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_posts_user_created ON posts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_posts_created_at ON posts (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_posts_likes_count ON posts (likes_count DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS post_likes (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_post_likes_post_user ON post_likes (post_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_post_likes_unique ON post_likes (post_id, user_id);

CREATE TABLE IF NOT EXISTS post_comments (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    text TEXT NOT NULL,
    parent_id INTEGER REFERENCES post_comments(id) ON DELETE CASCADE,
    likes_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_post_comments_post ON post_comments (post_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_post_comments_parent ON post_comments (parent_id);
CREATE INDEX IF NOT EXISTS ix_post_comments_post_created ON post_comments (post_id, created_at ASC);

CREATE TABLE IF NOT EXISTS comment_closure (
    ancestor_id INTEGER REFERENCES post_comments(id) ON DELETE CASCADE,
    descendant_id INTEGER REFERENCES post_comments(id) ON DELETE CASCADE,
    depth INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ancestor_id, descendant_id)
);
CREATE INDEX IF NOT EXISTS ix_comment_closure_descendant ON comment_closure (descendant_id);
CREATE INDEX IF NOT EXISTS ix_comment_closure_ancestor_depth ON comment_closure (ancestor_id, depth);
CREATE INDEX IF NOT EXISTS ix_comment_closure_covering ON comment_closure (ancestor_id, depth, descendant_id);

CREATE TABLE IF NOT EXISTS comment_likes (
    id SERIAL PRIMARY KEY,
    comment_id INTEGER NOT NULL REFERENCES post_comments(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_comment_likes_comment ON comment_likes (comment_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_comment_likes_unique ON comment_likes (comment_id, user_id);

CREATE TABLE IF NOT EXISTS hashtags (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    post_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_hashtags_post_count ON hashtags (post_count DESC);

CREATE TABLE IF NOT EXISTS post_hashtags (
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    hashtag_id INTEGER NOT NULL REFERENCES hashtags(id) ON DELETE CASCADE,
    PRIMARY KEY (post_id, hashtag_id)
);
CREATE INDEX IF NOT EXISTS ix_post_hashtags_hashtag ON post_hashtags (hashtag_id);

CREATE TABLE IF NOT EXISTS mentions (
    id SERIAL PRIMARY KEY,
    mentioned_user_id INTEGER NOT NULL REFERENCES users(id),
    mentioner_user_id INTEGER NOT NULL REFERENCES users(id),
    post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
    comment_id INTEGER REFERENCES post_comments(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_mentions_mentioned ON mentions (mentioned_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_mentions_post ON mentions (post_id);
CREATE INDEX IF NOT EXISTS ix_mentions_mentioner ON mentions (mentioner_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS polls (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    question VARCHAR(500) NOT NULL,
    total_votes INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS poll_options (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    text VARCHAR(200) NOT NULL,
    votes INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS poll_votes (
    id SERIAL PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    option_id INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- ═══════════════════════════════════════
-- NOTIFICATIONS & TELEMETRY
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS push_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expo_push_token VARCHAR(255) NOT NULL,
    device_type VARCHAR(10),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, expo_push_token)
);
CREATE INDEX IF NOT EXISTS ix_push_tokens_user ON push_tokens (user_id);

CREATE TABLE IF NOT EXISTS match_subscriptions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    notify_wickets VARCHAR(5) DEFAULT 'true',
    notify_boundaries VARCHAR(5) DEFAULT 'false',
    notify_match_events VARCHAR(5) DEFAULT 'true',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, match_id)
);
CREATE INDEX IF NOT EXISTS ix_match_subs_user ON match_subscriptions (user_id);
CREATE INDEX IF NOT EXISTS ix_match_subs_match ON match_subscriptions (match_id);

CREATE TABLE IF NOT EXISTS app_events (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    event_type VARCHAR(20) NOT NULL DEFAULT 'event',
    event_name VARCHAR(200) NOT NULL,
    message TEXT,
    context JSONB,
    platform VARCHAR(20),
    app_version VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_app_events_type_date ON app_events (event_type, created_at DESC);
