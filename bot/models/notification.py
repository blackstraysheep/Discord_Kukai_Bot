from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from bot.models.kukai import Kukai


class NotificationSchedule(Base, TimestampMixin):
    """A single scheduled notification for a kukai deadline."""

    __tablename__ = "notification_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 'submission_close' | 'selecting_close' | 'entry_close'
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Seconds before the deadline to fire (e.g. 86400 = 24h)
    offset_secs: Mapped[int] = mapped_column(Integer, nullable=False)
    # 'all' | 'incomplete' | 'admin'
    target: Mapped[str] = mapped_column(String(20), nullable=False, default="all")
    # NULL = kukai channel, -1 = DM, positive int = specific channel ID
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    fired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # APScheduler job id for cancellation
    job_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="notification_schedules")
    logs: Mapped[list["NotificationLog"]] = relationship(
        "NotificationLog", back_populates="schedule", cascade="all, delete-orphan"
    )


class NotificationLog(Base):
    """Record of a notification that was actually sent."""

    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notification_schedules.id", ondelete="CASCADE"), nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    schedule: Mapped["NotificationSchedule"] = relationship("NotificationSchedule", back_populates="logs")
