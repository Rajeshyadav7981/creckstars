"""partial index on deliveries (fielder_id, wicket_type) for catch leaderboards

Revision ID: a8c1f2e479d3
Revises: f4d8e2b91637
Create Date: 2026-05-31
"""
from alembic import op


revision = "a8c1f2e479d3"
down_revision = "f4d8e2b91637"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deliveries_fielder_wicket "
        "ON deliveries (fielder_id, wicket_type) "
        "WHERE is_wicket = TRUE AND fielder_id IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_deliveries_fielder_wicket")
