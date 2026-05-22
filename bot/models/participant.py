from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from bot.models.kukai import Kukai


class KukaiParticipant(Base, TimestampMixin):
    """Per-kukai participant profile for non-entry kukai settings."""

    __tablename__ = "kukai_participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    haigo: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="participants")

    __table_args__ = (UniqueConstraint("kukai_id", "user_id"),)
