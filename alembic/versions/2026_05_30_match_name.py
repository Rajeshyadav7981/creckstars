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
    op.add_column("matches", sa.Column("name", sa.String(length=200), nullable=True))


def downgrade():
    op.drop_column("matches", "name")
