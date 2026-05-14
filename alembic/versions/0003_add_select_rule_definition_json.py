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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "select_rule_templates" not in table_names and "vote_rule_templates" in table_names:
        op.rename_table("vote_rule_templates", "select_rule_templates")
        table_names.remove("vote_rule_templates")
        table_names.add("select_rule_templates")

    if "select_rule_templates" not in table_names:
        op.create_table(
            "select_rule_templates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("guild_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("is_default", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("guild_id", "name"),
        )
        table_names.add("select_rule_templates")

    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("select_rule_templates")}
    if "definition_json" not in columns:
        op.add_column(
            "select_rule_templates",
            sa.Column("definition_json", sa.Text(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "select_rule_templates" not in table_names:
        return
    columns = {col["name"] for col in inspector.get_columns("select_rule_templates")}
    if "definition_json" in columns:
        op.drop_column("select_rule_templates", "definition_json")
