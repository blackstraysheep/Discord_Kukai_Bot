"""add participation record visibility setting

Revision ID: 0019_participation_records
Revises: 0018_select_unique_sub
Create Date: 2026-07-18 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019_participation_records"
down_revision: Union[str, None] = "0018_select_unique_sub"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "participation_record_visibility" in _columns("guild_settings"):
        return
    op.add_column(
        "guild_settings",
        sa.Column(
            "participation_record_visibility",
            sa.String(length=20),
            nullable=False,
            server_default="private",
        ),
    )


def downgrade() -> None:
    if "participation_record_visibility" not in _columns("guild_settings"):
        return
    op.drop_column("guild_settings", "participation_record_visibility")
