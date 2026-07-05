"""widen channel visibility policy

Revision ID: 0017_widen_visibility
Revises: 0016_channel_visibility
Create Date: 2026-07-05 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017_widen_visibility"
down_revision: Union[str, None] = "0016_channel_visibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns("kukais")}


def upgrade() -> None:
    if "channel_visibility_policy" not in _column_names():
        return
    op.alter_column(
        "kukais",
        "channel_visibility_policy",
        existing_type=sa.String(length=30),
        type_=sa.String(length=50),
        existing_nullable=False,
    )


def downgrade() -> None:
    if "channel_visibility_policy" not in _column_names():
        return
    op.alter_column(
        "kukais",
        "channel_visibility_policy",
        existing_type=sa.String(length=50),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
