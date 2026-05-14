from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from bot.models.kukai import Kukai
    from bot.models.select import Select


class Submission(Base, TimestampMixin):
    """A haiku submitted by a participant.

    Raw NFC-normalized text is stored as-is.
    Discord Markdown escaping is applied only at display time.
    """

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    is_discarded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="submissions")
    published: Mapped[Optional["PublishedSubmission"]] = relationship(
        "PublishedSubmission", back_populates="submission", cascade="all, delete-orphan", uselist=False
    )
    selects: Mapped[list["Select"]] = relationship("Select", back_populates="submission")


class PublishedSubmission(Base):
    """Assigned display number after /kukai publish.

    Decoupled from Submission so rollback is a simple DELETE.
    """

    __tablename__ = "published_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True
    )
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # Randomly assigned display number (1..N)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    submission: Mapped["Submission"] = relationship("Submission", back_populates="published")

    __table_args__ = (UniqueConstraint("kukai_id", "number"),)
