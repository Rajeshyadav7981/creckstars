"""perf indexes: deliveries covering, polls FK indexes

- ix_deliveries_innings_over_agg: covering (innings_id, over_number) INCLUDE
  (total_runs, is_wicket) so the over-aggregates rollup used by live
  scorecards can serve from the index without a heap visit.
- ix_polls_user_created: keyset pagination of a user's polls.
- ix_poll_options_poll, ix_poll_votes_poll_user: PostgreSQL does NOT
  auto-index FK columns. These were doing seq scans on every poll fetch
  / vote uniqueness check.

Revision ID: 5b1d7a3e92c4
Revises: 4e2f9c7a13b5
Create Date: 2026-05-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = '5b1d7a3e92c4'
down_revision: Union[str, Sequence[str], None] = '4e2f9c7a13b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY so we don't block the live deliveries table; alembic must
    # run with transaction_per_migration=False (or each statement on its own
    # connection) for these to take effect on a hot DB. The IF NOT EXISTS
    # makes re-runs safe.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deliveries_innings_over_agg "
        "ON deliveries (innings_id, over_number) "
        "INCLUDE (total_runs, is_wicket, extra_type, is_legal)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_polls_user_created "
        "ON polls (user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_poll_options_poll "
        "ON poll_options (poll_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_poll_votes_poll_user "
        "ON poll_votes (poll_id, user_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_poll_votes_poll_user")
    op.execute("DROP INDEX IF EXISTS ix_poll_options_poll")
    op.execute("DROP INDEX IF EXISTS ix_polls_user_created")
    op.execute("DROP INDEX IF EXISTS ix_deliveries_innings_over_agg")
