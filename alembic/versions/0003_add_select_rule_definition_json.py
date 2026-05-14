"""add definition_json to select_rule_templates

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "select_rule_templates",
        sa.Column("definition_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("select_rule_templates", "definition_json")
