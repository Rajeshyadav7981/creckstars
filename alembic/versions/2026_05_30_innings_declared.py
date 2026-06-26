"""innings.declared — declared-closure flag

Revision ID: e2c91b475d8a
Revises: c5e8a17fb024
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa


revision = "e2c91b475d8a"
down_revision = "c5e8a17fb024"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent ALTER so a fresh build (psql -f schema.sql && alembic upgrade
    # head) stays stamp-free even if this column is later folded into the baseline.
    op.execute(
        "ALTER TABLE innings "
        "ADD COLUMN IF NOT EXISTS declared BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade():
    op.drop_column("innings", "declared")
