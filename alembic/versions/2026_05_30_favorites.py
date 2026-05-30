"""user_favorite_matches + user_favorite_tournaments

Revision ID: 7f4d2c8e1a93
Revises: a3f1c8d2e7b6
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa


revision = "7f4d2c8e1a93"
down_revision = "a3f1c8d2e7b6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_favorite_matches",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "match_id"),
    )
    op.create_index(
        "ix_user_favorite_matches_recent",
        "user_favorite_matches",
        ["user_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "user_favorite_tournaments",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tournament_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tournament_id"], ["tournaments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "tournament_id"),
    )
    op.create_index(
        "ix_user_favorite_tournaments_recent",
        "user_favorite_tournaments",
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade():
    op.drop_index("ix_user_favorite_tournaments_recent", table_name="user_favorite_tournaments")
    op.drop_table("user_favorite_tournaments")
    op.drop_index("ix_user_favorite_matches_recent", table_name="user_favorite_matches")
    op.drop_table("user_favorite_matches")
