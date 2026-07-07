"""make one select per selector and submission

Revision ID: 0018_select_unique_selector_submission
Revises: 0017_widen_visibility
Create Date: 2026-07-07 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018_select_unique_selector_submission"
down_revision: Union[str, None] = "0017_widen_visibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_COLUMNS = ["kukai_id", "selector_user_id", "submission_id", "select_label_id"]
NEW_COLUMNS = ["kukai_id", "selector_user_id", "submission_id"]
OLD_CONSTRAINT = "uq_selects_kukai_id_selector_user_id_submission_id_select_label_id"
NEW_CONSTRAINT = "uq_selects_kukai_selector_submission"
NAMING_CONVENTION = {
    "uq": "uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s_%(column_2_name)s_%(column_3_name)s",
}


def _unique_constraint_name(columns: list[str]) -> str | None:
    inspector = sa.inspect(op.get_bind())
    for constraint in inspector.get_unique_constraints("selects"):
        if constraint.get("column_names") == columns:
            return constraint.get("name")
    return None


def _has_unique_constraint(columns: list[str]) -> bool:
    return _unique_constraint_name(columns) is not None


def _deduplicate_selects_for_new_constraint() -> None:
    duplicate_ids = """
        SELECT id FROM (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY kukai_id, selector_user_id, submission_id
                    ORDER BY id
                ) AS rn
            FROM selects
        ) ranked
        WHERE rn > 1
    """
    op.execute(sa.text(f"DELETE FROM select_comments WHERE select_id IN ({duplicate_ids})"))
    op.execute(sa.text(f"DELETE FROM selects WHERE id IN ({duplicate_ids})"))


def upgrade() -> None:
    _deduplicate_selects_for_new_constraint()

    bind = op.get_bind()
    old_name = _unique_constraint_name(OLD_COLUMNS)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("selects", naming_convention=NAMING_CONVENTION) as batch_op:
            batch_op.drop_constraint(old_name or OLD_CONSTRAINT, type_="unique")
            batch_op.create_unique_constraint(NEW_CONSTRAINT, NEW_COLUMNS)
        return

    if old_name is not None:
        op.drop_constraint(old_name, "selects", type_="unique")
    if not _has_unique_constraint(NEW_COLUMNS):
        op.create_unique_constraint(NEW_CONSTRAINT, "selects", NEW_COLUMNS)


def downgrade() -> None:
    bind = op.get_bind()
    new_name = _unique_constraint_name(NEW_COLUMNS)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("selects", naming_convention=NAMING_CONVENTION) as batch_op:
            if new_name is not None:
                batch_op.drop_constraint(new_name, type_="unique")
            batch_op.create_unique_constraint(OLD_CONSTRAINT, OLD_COLUMNS)
        return

    if new_name is not None:
        op.drop_constraint(new_name, "selects", type_="unique")
    if not _has_unique_constraint(OLD_COLUMNS):
        op.create_unique_constraint(OLD_CONSTRAINT, "selects", OLD_COLUMNS)
