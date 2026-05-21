"""add admin thread id to kukais

Revision ID: 0008_admin_thread_id
Revises: 0007
Create Date: 2026-05-21 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_admin_thread_id"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kukais", sa.Column("admin_thread_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("kukais", "admin_thread_id")
