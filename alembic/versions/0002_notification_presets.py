"""notification_presets table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_presets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("entries_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("guild_id", "name"),
    )
    op.create_index("ix_notification_presets_guild_id", "notification_presets", ["guild_id"])


def downgrade() -> None:
    op.drop_index("ix_notification_presets_guild_id", "notification_presets")
    op.drop_table("notification_presets")
