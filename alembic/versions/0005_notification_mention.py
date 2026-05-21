"""add notification mention flag

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("notification_schedules", "mention"):
        op.add_column(
            "notification_schedules",
            sa.Column("mention", sa.Boolean(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if _has_column("notification_schedules", "mention"):
        op.drop_column("notification_schedules", "mention")
