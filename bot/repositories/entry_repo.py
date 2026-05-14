from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.entry import Entry


async def get(session: AsyncSession, entry_id: int) -> Entry | None:
    return await session.get(Entry, entry_id)


async def get_by_user(
    session: AsyncSession, kukai_id: int, user_id: int
) -> Entry | None:
    result = await session.execute(
        select(Entry).where(Entry.kukai_id == kukai_id, Entry.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_by_kukai(
    session: AsyncSession,
    kukai_id: int,
    status: str | None = None,
) -> list[Entry]:
    q = select(Entry).where(Entry.kukai_id == kukai_id)
    if status is not None:
        q = q.where(Entry.status == status)
    result = await session.execute(q.order_by(Entry.created_at))
    return list(result.scalars().all())


async def count_participants(
    session: AsyncSession, kukai_id: int, approval_mode: bool
) -> int:
    """Count effective participants.

    With approval: only 'approved' entries count.
    Without approval: 'pending' and 'approved' both count.
    """
    statuses = ["approved"] if approval_mode else ["pending", "approved"]
    result = await session.execute(
        select(func.count()).where(
            Entry.kukai_id == kukai_id,
            Entry.status.in_(statuses),
        )
    )
    return result.scalar_one()


async def has_haigo_conflict(
    session: AsyncSession,
    kukai_id: int,
    haigo: str,
    *,
    exclude_user_id: int | None = None,
) -> bool:
    normalized = haigo.strip().casefold()
    if not normalized:
        return False

    q = select(func.count()).where(
        Entry.kukai_id == kukai_id,
        Entry.status.in_(["pending", "approved"]),
        Entry.haigo.is_not(None),
        func.lower(func.trim(Entry.haigo)) == normalized,
    )
    if exclude_user_id is not None:
        q = q.where(Entry.user_id != exclude_user_id)

    result = await session.execute(q)
    return result.scalar_one() > 0
