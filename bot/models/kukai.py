from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from bot.models.entry import Entry
    from bot.models.notification import NotificationSchedule
    from bot.models.submission import Submission
    from bot.models.select import OverallSelectComment, Select
    from bot.models.select_rule import SelectLabel
    from bot.models.voice_session import VoiceSession


class Kukai(Base, TimestampMixin):
    __tablename__ = "kukais"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    theme: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # KukaiState value string
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", index=True)
    # Saved before pausing, restored on resume
    pre_pause_state: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # --- Deadlines ---
    entry_open_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    entry_close_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    submission_open_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    submission_close_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    selecting_open_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    selecting_close_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    results_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # --- Entry settings ---
    entry_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    entry_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    min_participants: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 'admin' | 'auto_cancel'
    min_participants_action: Mapped[str] = mapped_column(String(20), nullable=False, default="admin")

    # --- Submission settings ---
    submission_min: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    submission_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=5)
    submission_overflow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    submission_underflow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 'manual' | 'semi_auto' | 'full_auto'
    submission_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    # 'keep' | 'discard'
    submission_incomplete: Mapped[str] = mapped_column(String(10), nullable=False, default="keep")

    # --- Selecting settings ---
    selecting_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    selecting_incomplete: Mapped[str] = mapped_column(String(10), nullable=False, default="keep")
    points_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Publish / result settings ---
    # 'auto' | 'manual'
    publish_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")
    result_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")
    author_reveal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Whether to show authors whose total score <= 0
    author_reveal_zero: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 'score' | 'number' | 'author'
    result_display_default: Mapped[str] = mapped_column(String(10), nullable=False, default="score")

    # --- Notification ---
    # NULL = kukai channel, -1 = DM, -2 = admin thread, positive int = specific channel ID
    notify_channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # Private thread used for operation/admin-only notices.
    admin_thread_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # --- Stored Discord message IDs ---
    submission_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    result_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # --- Relationships ---
    admins: Mapped[list["KukaiAdmin"]] = relationship(
        "KukaiAdmin", back_populates="kukai", cascade="all, delete-orphan"
    )
    select_labels: Mapped[list["SelectLabel"]] = relationship(
        "SelectLabel", back_populates="kukai", cascade="all, delete-orphan",
        order_by="SelectLabel.display_order",
    )
    entries: Mapped[list["Entry"]] = relationship(
        "Entry", back_populates="kukai", cascade="all, delete-orphan"
    )
    submissions: Mapped[list["Submission"]] = relationship(
        "Submission", back_populates="kukai", cascade="all, delete-orphan"
    )
    selects: Mapped[list["Select"]] = relationship(
        "Select", back_populates="kukai", cascade="all, delete-orphan"
    )
    overall_comments: Mapped[list["OverallSelectComment"]] = relationship(
        "OverallSelectComment", back_populates="kukai", cascade="all, delete-orphan"
    )
    notification_schedules: Mapped[list["NotificationSchedule"]] = relationship(
        "NotificationSchedule", back_populates="kukai", cascade="all, delete-orphan"
    )
    voice_session: Mapped[Optional["VoiceSession"]] = relationship(
        "VoiceSession", back_populates="kukai", cascade="all, delete-orphan", uselist=False
    )


class KukaiAdmin(Base, TimestampMixin):
    __tablename__ = "kukai_admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    added_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="admins")

    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("kukai_id", "user_id"),
    )
