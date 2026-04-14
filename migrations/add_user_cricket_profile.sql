-- Add cricket profile fields to users table
-- Run once on existing databases. Safe to rerun.

ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS city VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS state_province VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS country VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS date_of_birth DATE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS batting_style VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS bowling_style VARCHAR(30);
ALTER TABLE users ADD COLUMN IF NOT EXISTS player_role VARCHAR(20);
