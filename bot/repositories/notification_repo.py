"""Notification repository: DB access for notification schedules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.notification import NotificationSchedule


async def get_schedules_for_kukai(
    session: AsyncSession, kukai_id: int, *, unfired_only: bool = False
) -> list[NotificationSchedule]:
    stmt = select(NotificationSchedule).where(NotificationSchedule.kukai_id == kukai_id)
    if unfired_only:
        stmt = stmt.where(NotificationSchedule.fired == False)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_schedule(session: AsyncSession, schedule_id: int) -> NotificationSchedule | None:
    return await session.get(NotificationSchedule, schedule_id)
