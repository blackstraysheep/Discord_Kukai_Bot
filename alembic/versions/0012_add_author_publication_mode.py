"""add author publication mode

Revision ID: 0012_add_author_publication_mode
Revises: 0011_add_kukai_participants
Create Date: 2026-05-27 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_add_author_publication_mode"
down_revision: Union[str, None] = "0011_add_kukai_participants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "kukais",
        sa.Column(
            "author_publication_mode",
            sa.String(length=20),
            nullable=False,
            server_default="with_result",
        ),
    )
    op.execute(
        "UPDATE kukais SET author_publication_mode = "
        "CASE WHEN author_reveal THEN 'with_result' ELSE 'never' END"
    )


def downgrade() -> None:
    op.drop_column("kukais", "author_publication_mode")
