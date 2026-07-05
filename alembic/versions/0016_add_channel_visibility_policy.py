"""add channel visibility policy

Revision ID: 0016_add_channel_visibility_policy
Revises: 0015_add_portal_channel_id
Create Date: 2026-07-05 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0016_add_channel_visibility_policy"
down_revision: Union[str, None] = "0015_add_portal_channel_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns("kukais")}


def upgrade() -> None:
    if "channel_visibility_policy" in _column_names():
        return
    op.add_column(
        "kukais",
        sa.Column(
            "channel_visibility_policy",
            sa.String(length=30),
            nullable=False,
            server_default="public",
        ),
    )
    op.alter_column("kukais", "channel_visibility_policy", server_default=None)


def downgrade() -> None:
    if "channel_visibility_policy" not in _column_names():
        return
    op.drop_column("kukais", "channel_visibility_policy")
