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
    op.add_column(
        "tournament_stages",
        sa.Column("stage_label", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tournament_stages", "stage_label")
