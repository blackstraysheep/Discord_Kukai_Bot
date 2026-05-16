"""initial

Revision ID: 0001
Revises:
Create Date: 2026-05-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guild_settings",
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("create_role", sa.String(20), nullable=False, server_default="everyone"),
        sa.Column("create_role_ids", sa.String(), nullable=False, server_default="[]"),
        sa.Column("create_user_ids", sa.String(), nullable=False, server_default="[]"),
        sa.Column("common_vc_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "kukais",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("theme", sa.String(200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("pre_pause_state", sa.String(30), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        # Deadlines
        sa.Column("entry_open_at", sa.DateTime(), nullable=True),
        sa.Column("entry_close_at", sa.DateTime(), nullable=True),
        sa.Column("submission_open_at", sa.DateTime(), nullable=True),
        sa.Column("submission_close_at", sa.DateTime(), nullable=True),
        sa.Column("selecting_open_at", sa.DateTime(), nullable=True),
        sa.Column("selecting_close_at", sa.DateTime(), nullable=True),
        sa.Column("results_at", sa.DateTime(), nullable=True),
        # Entry settings
        sa.Column("entry_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("entry_approval", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("min_participants", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_participants_action", sa.String(20), nullable=False, server_default="admin"),
        # Submission settings
        sa.Column("submission_min", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("submission_max", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("submission_overflow", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("submission_underflow", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("submission_mode", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("submission_incomplete", sa.String(10), nullable=False, server_default="keep"),
        # Selecting settings
        sa.Column("selecting_mode", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("selecting_incomplete", sa.String(10), nullable=False, server_default="keep"),
        sa.Column("points_enabled", sa.Boolean(), nullable=False, server_default="1"),
        # Publish / result settings
        sa.Column("publish_mode", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("result_mode", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("author_reveal", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("author_reveal_zero", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("result_display_default", sa.String(10), nullable=False, server_default="score"),
        # Notification
        sa.Column("notify_channel_id", sa.BigInteger(), nullable=True),
        # Stored Discord message IDs
        sa.Column("submission_message_id", sa.BigInteger(), nullable=True),
        sa.Column("result_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_kukais_guild", "kukais", ["guild_id"])
    op.create_index("idx_kukais_state", "kukais", ["state"])

    op.create_table(
        "kukai_admins",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kukai_id", sa.Integer(), sa.ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kukai_id", "user_id"),
    )
    op.create_index("idx_kadmin_kukai", "kukai_admins", ["kukai_id"])

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

    op.create_table(
        "select_labels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kukai_id", sa.Integer(), sa.ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("point", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rank_priority", sa.Integer(), nullable=False),
        sa.Column("min_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_count", sa.Integer(), nullable=True),
        sa.Column("comment_mode", sa.String(10), nullable=False, server_default="none"),
        sa.UniqueConstraint("kukai_id", "label"),
    )
    op.create_index("idx_vlabel_kukai", "select_labels", ["kukai_id"])

    op.create_table(
        "entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kukai_id", sa.Integer(), sa.ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("haigo", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("is_special", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kukai_id", "user_id"),
    )
    op.create_index("idx_entry_kukai", "entries", ["kukai_id"])
    op.create_index("idx_entry_user", "entries", ["user_id"])
    op.create_index("idx_entry_status", "entries", ["kukai_id", "status"])

    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kukai_id", sa.Integer(), sa.ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("is_discarded", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_sub_kukai", "submissions", ["kukai_id"])
    op.create_index("idx_sub_user", "submissions", ["kukai_id", "user_id"])

    op.create_table(
        "published_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kukai_id", sa.Integer(), sa.ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kukai_id", "number"),
    )
    op.create_index("idx_pubsub_kukai", "published_submissions", ["kukai_id"])

    op.create_table(
        "selects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kukai_id", sa.Integer(), sa.ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False),
        sa.Column("selector_user_id", sa.BigInteger(), nullable=False),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("select_label_id", sa.Integer(), sa.ForeignKey("select_labels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_self_comment", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kukai_id", "selector_user_id", "submission_id", "select_label_id"),
    )
    op.create_index("idx_select_kukai", "selects", ["kukai_id"])
    op.create_index("idx_select_selector", "selects", ["kukai_id", "selector_user_id"])
    op.create_index("idx_select_sub", "selects", ["submission_id"])

    op.create_table(
        "select_comments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("select_id", sa.Integer(), sa.ForeignKey("selects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "overall_comments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kukai_id", sa.Integer(), sa.ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kukai_id", "user_id"),
    )

    op.create_table(
        "notification_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kukai_id", sa.Integer(), sa.ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("offset_secs", sa.Integer(), nullable=False),
        sa.Column("target", sa.String(20), nullable=False, server_default="all"),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("mention", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("fired", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("job_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_notif_kukai", "notification_schedules", ["kukai_id"])

    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("notification_schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
    )

    op.create_table(
        "voice_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kukai_id", sa.Integer(), sa.ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("vc_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("end_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("voice_sessions")
    op.drop_table("notification_logs")
    op.drop_table("notification_schedules")
    op.drop_table("overall_comments")
    op.drop_table("select_comments")
    op.drop_table("selects")
    op.drop_table("published_submissions")
    op.drop_table("submissions")
    op.drop_table("entries")
    op.drop_table("select_labels")
    op.drop_table("select_rule_templates")
    op.drop_table("kukai_admins")
    op.drop_table("kukais")
    op.drop_table("guild_settings")
