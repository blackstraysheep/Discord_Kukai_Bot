"""add portal channel id to guild settings

Revision ID: 0015_add_portal_channel_id
Revises: 0014_select_comments_select_id
Create Date: 2026-07-03 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015_add_portal_channel_id"
down_revision: Union[str, None] = "0014_select_comments_select_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns("guild_settings")}


def upgrade() -> None:
    if "portal_channel_id" in _column_names():
        return
    op.add_column("guild_settings", sa.Column("portal_channel_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    if "portal_channel_id" not in _column_names():
        return
    op.drop_column("guild_settings", "portal_channel_id")
