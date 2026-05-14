from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from bot.models.kukai import Kukai


class VoiceSession(Base, TimestampMixin):
    """Optional voice channel session linked to a kukai."""

    __tablename__ = "voice_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    vc_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="voice_session")
