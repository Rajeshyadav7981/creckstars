-- Ensures the deliveries.commentary column exists.
-- Safe to run repeatedly (idempotent via IF NOT EXISTS).
ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS commentary TEXT;
