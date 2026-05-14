from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from bot.models.kukai import Kukai
    from bot.models.submission import Submission
    from bot.models.vote_rule import VoteLabel


class Vote(Base, TimestampMixin):
    """A single vote cast by a participant on a published submission."""

    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True
    )
    voter_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vote_label_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vote_labels.id", ondelete="CASCADE"), nullable=False
    )
    # True when the author adds a comment on their own haiku (point=0, shown last)
    is_self_comment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="votes")
    submission: Mapped["Submission"] = relationship("Submission", back_populates="votes")
    vote_label: Mapped["VoteLabel"] = relationship("VoteLabel", back_populates="votes")
    comment: Mapped[Optional["VoteComment"]] = relationship(
        "VoteComment", back_populates="vote", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        UniqueConstraint("kukai_id", "voter_user_id", "submission_id", "vote_label_id"),
    )


class VoteComment(Base, TimestampMixin):
    """Optional/required comment attached to a single Vote."""

    __tablename__ = "vote_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vote_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("votes.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    comment: Mapped[str] = mapped_column(Text, nullable=False)

    vote: Mapped["Vote"] = relationship("Vote", back_populates="comment")


class OverallComment(Base, TimestampMixin):
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
