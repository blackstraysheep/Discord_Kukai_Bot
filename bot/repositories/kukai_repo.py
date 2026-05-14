from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.kukai import Kukai, KukaiAdmin
from bot.state_machine.states import KukaiState


async def get(session: AsyncSession, kukai_id: int) -> Kukai | None:
    return await session.get(Kukai, kukai_id)


async def list_active(session: AsyncSession, guild_id: int) -> list[Kukai]:
    """Return kukais that are not ended or cancelled, newest first."""
    result = await session.execute(
        select(Kukai)
        .where(Kukai.guild_id == guild_id)
        .where(Kukai.state.notin_([KukaiState.ENDED, KukaiState.CANCELLED]))
        .order_by(Kukai.created_at.desc())
    )
    return list(result.scalars().all())


async def list_all(session: AsyncSession, guild_id: int) -> list[Kukai]:
    result = await session.execute(
        select(Kukai)
        .where(Kukai.guild_id == guild_id)
        .order_by(Kukai.created_at.desc())
    )
    return list(result.scalars().all())


async def is_admin(session: AsyncSession, kukai_id: int, user_id: int) -> bool:
    result = await session.execute(
        select(KukaiAdmin).where(
            KukaiAdmin.kukai_id == kukai_id,
            KukaiAdmin.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def add_admin(
    session: AsyncSession, kukai_id: int, user_id: int, added_by: int
) -> KukaiAdmin:
    admin = KukaiAdmin(kukai_id=kukai_id, user_id=user_id, added_by=added_by)
    session.add(admin)
    await session.flush()
    return admin


async def remove_admin(
    session: AsyncSession, kukai_id: int, user_id: int
) -> bool:
    result = await session.execute(
        select(KukaiAdmin).where(
            KukaiAdmin.kukai_id == kukai_id,
            KukaiAdmin.user_id == user_id,
        )
    )
    admin = result.scalar_one_or_none()
    if admin:
        await session.delete(admin)
        return True
    return False
