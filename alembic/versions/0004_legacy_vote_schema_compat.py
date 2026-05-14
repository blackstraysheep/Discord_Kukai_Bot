"""legacy vote schema compatibility (rename/add selecting/select columns)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _table_names(inspector)

    # Table renames (old -> new)
    if "select_rule_templates" not in tables and "vote_rule_templates" in tables:
        op.rename_table("vote_rule_templates", "select_rule_templates")
    if "select_labels" not in tables and "vote_labels" in tables:
        op.rename_table("vote_labels", "select_labels")
    if "selects" not in tables and "votes" in tables:
        op.rename_table("votes", "selects")
    if "select_comments" not in tables and "vote_comments" in tables:
        op.rename_table("vote_comments", "select_comments")
    if "overall_comments" not in tables and "overall_vote_comments" in tables:
        op.rename_table("overall_vote_comments", "overall_comments")

    inspector = sa.inspect(bind)
    tables = _table_names(inspector)

    # kukais: selecting_* columns
    if "kukais" in tables:
        kukai_cols = _column_names(inspector, "kukais")
        with op.batch_alter_table("kukais") as batch_op:
            if "selecting_open_at" not in kukai_cols:
                if "voting_open_at" in kukai_cols:
                    batch_op.alter_column(
                        "voting_open_at",
                        new_column_name="selecting_open_at",
                        existing_type=sa.DateTime(),
                    )
                else:
                    batch_op.add_column(sa.Column("selecting_open_at", sa.DateTime(), nullable=True))

            if "selecting_close_at" not in kukai_cols:
                if "voting_close_at" in kukai_cols:
                    batch_op.alter_column(
                        "voting_close_at",
                        new_column_name="selecting_close_at",
                        existing_type=sa.DateTime(),
                    )
                else:
                    batch_op.add_column(sa.Column("selecting_close_at", sa.DateTime(), nullable=True))

            if "selecting_mode" not in kukai_cols:
                if "voting_mode" in kukai_cols:
                    batch_op.alter_column(
                        "voting_mode",
                        new_column_name="selecting_mode",
                        existing_type=sa.String(length=20),
                    )
                else:
                    batch_op.add_column(
                        sa.Column(
                            "selecting_mode",
                            sa.String(length=20),
                            nullable=False,
                            server_default="manual",
                        )
                    )

            if "selecting_incomplete" not in kukai_cols:
                if "voting_incomplete" in kukai_cols:
                    batch_op.alter_column(
                        "voting_incomplete",
                        new_column_name="selecting_incomplete",
                        existing_type=sa.String(length=10),
                    )
                else:
                    batch_op.add_column(
                        sa.Column(
                            "selecting_incomplete",
                            sa.String(length=10),
                            nullable=False,
                            server_default="keep",
                        )
                    )

    inspector = sa.inspect(bind)
    tables = _table_names(inspector)

    # selects: renamed columns + self-comment column
    if "selects" in tables:
        select_cols = _column_names(inspector, "selects")
        with op.batch_alter_table("selects") as batch_op:
            if "selector_user_id" not in select_cols and "voter_user_id" in select_cols:
                batch_op.alter_column(
                    "voter_user_id",
                    new_column_name="selector_user_id",
                    existing_type=sa.BigInteger(),
                )
            if "select_label_id" not in select_cols and "vote_label_id" in select_cols:
                batch_op.alter_column(
                    "vote_label_id",
                    new_column_name="select_label_id",
                    existing_type=sa.Integer(),
                )
            if "is_self_comment" not in select_cols:
                batch_op.add_column(
                    sa.Column(
                        "is_self_comment",
                        sa.Boolean(),
                        nullable=False,
                        server_default="0",
                    )
                )


def downgrade() -> None:
    # Compatibility migration: no-op downgrade.
    pass
