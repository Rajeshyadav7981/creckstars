"""baseline — snapshot of existing schema.sql state

Empty upgrade/downgrade: this revision exists only to stamp pre-Alembic
databases. Tables, indexes, and constraints present before Alembic was
adopted are assumed to already exist (created by schema.sql). All schema
changes from this point forward live in subsequent revisions.

Revision ID: 812e715ead58
Revises:
Create Date: 2026-04-23 23:03:34.771594
"""
from typing import Sequence, Union


revision: str = '812e715ead58'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
