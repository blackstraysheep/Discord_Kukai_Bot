from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from bot.models.kukai import Kukai


class Entry(Base, TimestampMixin):
    """Participant registration for a kukai."""

    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # Pen name; NULL means use the user's server display name
    haigo: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # 'pending' | 'approved' | 'rejected' | 'withdrawn'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    # Special participants can bypass submission/vote count limits
    is_special: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="entries")

    __table_args__ = (UniqueConstraint("kukai_id", "user_id"),)
