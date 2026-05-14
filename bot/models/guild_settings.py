from typing import Optional

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base, TimestampMixin


class GuildSettings(Base, TimestampMixin):
    __tablename__ = "guild_settings"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 'everyone' | 'role' | 'admin' | 'owner' | 'specific'
    create_role: Mapped[str] = mapped_column(String(20), nullable=False, default="everyone")
    # JSON list of role snowflakes (used when create_role='role')
    create_role_ids: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    # JSON list of user snowflakes (used when create_role='specific')
    create_user_ids: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    common_vc_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
