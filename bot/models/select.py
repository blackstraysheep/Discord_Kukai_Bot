from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from bot.models.kukai import Kukai
    from bot.models.submission import Submission
    from bot.models.select_rule import SelectLabel


class Select(Base, TimestampMixin):
    """A single selection cast by a participant on a published submission."""

    __tablename__ = "selects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True
    )
    selector_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    select_label_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("select_labels.id", ondelete="CASCADE"), nullable=False
    )
    # True when the author adds a comment on their own haiku (point=0, shown last)
    is_self_comment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="selects")
    submission: Mapped["Submission"] = relationship("Submission", back_populates="selects")
    select_label: Mapped["SelectLabel"] = relationship("SelectLabel", back_populates="selects")
    comment: Mapped[Optional["SelectComment"]] = relationship(
        "SelectComment", back_populates="select", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        UniqueConstraint(
            "kukai_id",
            "selector_user_id",
            "submission_id",
            name="uq_selects_kukai_selector_submission",
        ),
    )


class SelectComment(Base, TimestampMixin):
    """Optional/required comment attached to a single Select."""

    __tablename__ = "select_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    select_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("selects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    comment: Mapped[str] = mapped_column(Text, nullable=False)

    select: Mapped["Select"] = relationship("Select", back_populates="comment")


class OverallSelectComment(Base, TimestampMixin):
    """Overall comment (総評) on the kukai by a participant."""

    __tablename__ = "overall_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="overall_comments")

    __table_args__ = (UniqueConstraint("kukai_id", "user_id"),)
