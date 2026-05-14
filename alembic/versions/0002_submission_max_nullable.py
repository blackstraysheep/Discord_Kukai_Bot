"""make submission_max nullable (NULL means unlimited)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Legacy "practical unlimited" values are migrated to NULL.
    op.execute(sa.text("UPDATE kukais SET submission_max = NULL WHERE submission_max >= 999"))
    with op.batch_alter_table("kukais") as batch_op:
        batch_op.alter_column("submission_max", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute(sa.text("UPDATE kukais SET submission_max = 3 WHERE submission_max IS NULL"))
    with op.batch_alter_table("kukais") as batch_op:
        batch_op.alter_column("submission_max", existing_type=sa.Integer(), nullable=False)
