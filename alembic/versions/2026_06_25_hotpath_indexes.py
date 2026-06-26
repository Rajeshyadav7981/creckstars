"""hot-path scoring indexes — partnerships(active) + fall_of_wickets(delivery)

The live-scoring path calls get_active_partnership() on every delivery, filtering
partnerships by (innings_id, is_active=True); only innings_id was indexed, forcing
a scan of the innings' partnership rows per ball. Undo looks up fall_of_wickets by
delivery_id, which had no index. Both are added here.

Revision ID: b7d3f9a1c204
Revises: a8c1f2e479d3
Create Date: 2026-06-25
"""
from alembic import op


revision = "b7d3f9a1c204"
down_revision = "a8c1f2e479d3"
branch_labels = None
depends_on = None


def upgrade():
    # Partial index: only the single active partnership per innings is indexed,
    # keeping it tiny and making the per-ball lookup an index hit.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_partnerships_innings_active "
        "ON partnerships (innings_id) WHERE is_active = TRUE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fall_of_wickets_delivery "
        "ON fall_of_wickets (delivery_id)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_partnerships_innings_active")
    op.execute("DROP INDEX IF EXISTS ix_fall_of_wickets_delivery")
