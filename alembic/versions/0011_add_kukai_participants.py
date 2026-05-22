"""add kukai participant profiles

Revision ID: 0011_add_kukai_participants
Revises: 0010_add_entry_mode
Create Date: 2026-05-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_add_kukai_participants"
down_revision: Union[str, None] = "0010_add_entry_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kukai_participants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kukai_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("haigo", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kukai_id"], ["kukais.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kukai_id", "user_id"),
    )
    op.create_index(op.f("ix_kukai_participants_kukai_id"), "kukai_participants", ["kukai_id"])
    op.create_index(op.f("ix_kukai_participants_user_id"), "kukai_participants", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_kukai_participants_user_id"), table_name="kukai_participants")
    op.drop_index(op.f("ix_kukai_participants_kukai_id"), table_name="kukai_participants")
    op.drop_table("kukai_participants")
