"""enable earthdistance + GIST index for venues nearby

Replaces the seq-scan Haversine with an indexed geo lookup. `cube` and
`earthdistance` ship in postgres-contrib (already in use here for pg_trgm).

Query path enabled by this migration:
  ll_to_earth(lat, lng) <@ earth_box(ll_to_earth(:lat, :lng), :radius_m)
  AND earth_distance(ll_to_earth(:lat, :lng), ll_to_earth(lat, lng)) <= :radius_m

The bbox `<@` predicate uses the GIST index; the earth_distance refinement
trims the bbox corners back to a true circle. ~10–100× faster than the old
6371*acos(...) seq scan on 10k+ rows, and accurate to a few meters.

Revision ID: 4e2f9c7a13b5
Revises: 1ab6ec1834ac
Create Date: 2026-04-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = '4e2f9c7a13b5'
down_revision: Union[str, Sequence[str], None] = '1ab6ec1834ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS cube")
    op.execute("CREATE EXTENSION IF NOT EXISTS earthdistance")
    # IMMUTABLE-on-IMMUTABLE composition; safe to index.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_venues_earth "
        "ON venues USING gist (ll_to_earth(latitude, longitude)) "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_venues_earth")
    # Leave the extensions installed — dropping them would break any other
    # consumer and is essentially never the right thing in a migration.
