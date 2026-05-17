"""add discord_event_id to voice_sessions

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("voice_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("discord_event_id", sa.BigInteger(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("voice_sessions") as batch_op:
        batch_op.drop_column("discord_event_id")
