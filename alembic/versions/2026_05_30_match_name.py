"""matches.name — optional display name

Revision ID: c5e8a17fb024
Revises: 7f4d2c8e1a93
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa


revision = "c5e8a17fb024"
down_revision = "7f4d2c8e1a93"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent ALTER so a fresh build (psql -f schema.sql && alembic upgrade
    # head) stays stamp-free even if this column is later folded into the baseline.
    op.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS name VARCHAR(200)")


def downgrade():
    op.drop_column("matches", "name")
