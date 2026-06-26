"""Add stage_label to tournament_stages — optional admin-set display name.

Revision ID: 2026_05_29_stage_label
Revises: 2026_05_05_perf_indexes
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa


revision = "a3f1c8d2e7b6"
down_revision = "5b1d7a3e92c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: this column is also present in the frozen baseline snapshot
    # (schema.sql), so on a fresh build the column already exists when this
    # delta runs. Idempotent ALTER lets `psql -f schema.sql && alembic upgrade
    # head` succeed with no manual stamping.
    op.execute(
        "ALTER TABLE tournament_stages "
        "ADD COLUMN IF NOT EXISTS stage_label VARCHAR(80)"
    )


def downgrade() -> None:
    op.drop_column("tournament_stages", "stage_label")
