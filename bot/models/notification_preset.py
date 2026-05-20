from sqlalchemy import BigInteger, Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base, TimestampMixin


class NotificationPreset(Base, TimestampMixin):
    __tablename__ = "notification_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entries_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    __table_args__ = (UniqueConstraint("guild_id", "name"),)
