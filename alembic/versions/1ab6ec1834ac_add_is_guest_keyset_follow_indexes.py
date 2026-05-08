"""add is_guest + keyset follow indexes

- players.is_guest: TeamSnap-style escape hatch — phoneless walk-in / kid players
  that must never auto-link to a real user.
- ix_players_mobile_stub: speeds up the link-on-register lookup for user_id IS NULL.
- ix_follows_{follower,following}_created: covering indexes for keyset pagination
  on the followers/following lists (replaces offset-based scan).

Revision ID: 1ab6ec1834ac
Revises: 812e715ead58
Create Date: 2026-04-23 23:03:59.399523
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1ab6ec1834ac'
down_revision: Union[str, Sequence[str], None] = '812e715ead58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE players ADD COLUMN IF NOT EXISTS is_guest "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_players_mobile_stub "
        "ON players (mobile) WHERE user_id IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_follows_following_created "
        "ON user_follows (following_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_follows_follower_created "
        "ON user_follows (follower_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_follows_follower_created")
    op.execute("DROP INDEX IF EXISTS ix_follows_following_created")
    op.execute("DROP INDEX IF EXISTS ix_players_mobile_stub")
    op.execute("ALTER TABLE players DROP COLUMN IF EXISTS is_guest")
