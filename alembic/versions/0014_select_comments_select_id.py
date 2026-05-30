"""rename select_comments vote_id to select_id

Revision ID: 0014_select_comments_select_id
Revises: 0013_select_comments_vote_id
Create Date: 2026-05-30 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014_select_comments_select_id"
down_revision: Union[str, None] = "0013_select_comments_vote_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns("select_comments")}


def upgrade() -> None:
    cols = _column_names()
    if "select_id" in cols:
        return
    if "vote_id" in cols:
        with op.batch_alter_table("select_comments") as batch_op:
            batch_op.alter_column(
                "vote_id",
                new_column_name="select_id",
                existing_type=sa.Integer(),
                existing_nullable=False,
            )


def downgrade() -> None:
    cols = _column_names()
    if "vote_id" in cols:
        return
    if "select_id" in cols:
        with op.batch_alter_table("select_comments") as batch_op:
            batch_op.alter_column(
                "select_id",
                new_column_name="vote_id",
                existing_type=sa.Integer(),
                existing_nullable=False,
            )
