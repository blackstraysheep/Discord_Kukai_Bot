"""add entry progress mode

Revision ID: 0010_add_entry_mode
Revises: 0009_drop_submission_max_default
Create Date: 2026-05-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_add_entry_mode"
down_revision: Union[str, None] = "0009_drop_submission_max_default"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("kukais") as batch_op:
        batch_op.add_column(
            sa.Column("entry_mode", sa.String(length=20), nullable=False, server_default="manual")
        )
    with op.batch_alter_table("kukais") as batch_op:
        batch_op.alter_column("entry_mode", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("kukais") as batch_op:
        batch_op.drop_column("entry_mode")
