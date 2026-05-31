"""GIN trigram indexes on the newly-searched columns

Adds pg_trgm GIN indexes on:
  - tournaments.location
  - tournaments.organizer_name
  - matches.name

These let ILIKE '%foo%' queries use the index instead of a sequential scan.
Without these, the new tournament-search broadening (location, organizer_name)
and match-search broadening (matches.name) would force a Seq Scan at scale.

Revision ID: f4d8e2b91637
Revises: e2c91b475d8a
Create Date: 2026-05-31
"""
from alembic import op


revision = "f4d8e2b91637"
down_revision = "e2c91b475d8a"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tournaments_location_trgm "
        "ON tournaments USING gin (location gin_trgm_ops) "
        "WHERE location IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tournaments_organizer_trgm "
        "ON tournaments USING gin (organizer_name gin_trgm_ops) "
        "WHERE organizer_name IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_matches_name_trgm "
        "ON matches USING gin (name gin_trgm_ops) "
        "WHERE name IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_tournaments_location_trgm")
    op.execute("DROP INDEX IF EXISTS ix_tournaments_organizer_trgm")
    op.execute("DROP INDEX IF EXISTS ix_matches_name_trgm")
