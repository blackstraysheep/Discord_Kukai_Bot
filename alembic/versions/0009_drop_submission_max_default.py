"""drop submission_max server default

Revision ID: 0009_drop_submission_max_default
Revises: 0008_admin_thread_id
Create Date: 2026-05-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_drop_submission_max_default"
down_revision: Union[str, None] = "0008_admin_thread_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("kukais") as batch_op:
        batch_op.alter_column(
            "submission_max",
            existing_type=sa.Integer(),
            nullable=True,
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("kukais") as batch_op:
        batch_op.alter_column(
            "submission_max",
            existing_type=sa.Integer(),
            nullable=True,
            server_default="5",
        )
