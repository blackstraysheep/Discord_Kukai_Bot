from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.notification_preset import NotificationPreset


async def get_by_guild(session: AsyncSession, guild_id: int) -> list[NotificationPreset]:
    result = await session.execute(
        select(NotificationPreset)
        .where(NotificationPreset.guild_id == guild_id)
        .order_by(NotificationPreset.name)
    )
    return list(result.scalars().all())


async def get_by_name(
    session: AsyncSession, guild_id: int, name: str
) -> NotificationPreset | None:
    result = await session.execute(
        select(NotificationPreset).where(
            NotificationPreset.guild_id == guild_id,
            NotificationPreset.name == name,
        )
    )
    return result.scalar_one_or_none()


async def get_default(session: AsyncSession, guild_id: int) -> NotificationPreset | None:
    result = await session.execute(
        select(NotificationPreset).where(
            NotificationPreset.guild_id == guild_id,
            NotificationPreset.is_default.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def set_default(
    session: AsyncSession, guild_id: int, preset_id: int | None
) -> None:
    await session.execute(
        update(NotificationPreset)
        .where(NotificationPreset.guild_id == guild_id)
        .values(is_default=False)
    )
    if preset_id is not None:
        await session.execute(
            update(NotificationPreset)
            .where(
                NotificationPreset.guild_id == guild_id,
                NotificationPreset.id == preset_id,
            )
            .values(is_default=True)
        )
