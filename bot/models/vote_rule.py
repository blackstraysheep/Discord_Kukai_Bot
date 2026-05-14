from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from bot.models.kukai import Kukai
    from bot.models.vote import Vote


class VoteRuleTemplate(Base, TimestampMixin):
    __tablename__ = "vote_rule_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_default: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint("guild_id", "name"),)


class VoteLabel(Base):
    """One row per selectable vote category within a kukai.

    Copied from a template at kukai creation and then independent.
    Negative points are allowed (e.g. 逆選).
    """

    __tablename__ = "vote_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kukai_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kukais.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    point: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rank_priority: Mapped[int] = mapped_column(Integer, nullable=False)
    min_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 'none' | 'optional' | 'required'
    comment_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="none")

    kukai: Mapped["Kukai"] = relationship("Kukai", back_populates="vote_labels")
    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="vote_label")

    __table_args__ = (UniqueConstraint("kukai_id", "label"),)
