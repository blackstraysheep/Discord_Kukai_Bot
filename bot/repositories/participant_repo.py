from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.participant import KukaiParticipant


async def get_by_user(
    session: AsyncSession, kukai_id: int, user_id: int
) -> KukaiParticipant | None:
    result = await session.execute(
        select(KukaiParticipant).where(
            KukaiParticipant.kukai_id == kukai_id,
            KukaiParticipant.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_by_kukai(session: AsyncSession, kukai_id: int) -> list[KukaiParticipant]:
    result = await session.execute(
        select(KukaiParticipant)
        .where(KukaiParticipant.kukai_id == kukai_id)
        .order_by(KukaiParticipant.created_at)
    )
    return list(result.scalars().all())


async def upsert(
    session: AsyncSession,
    kukai_id: int,
    user_id: int,
    *,
    haigo: str | None,
) -> KukaiParticipant:
    participant = await get_by_user(session, kukai_id, user_id)
    if participant is None:
        participant = KukaiParticipant(kukai_id=kukai_id, user_id=user_id, haigo=haigo)
        session.add(participant)
        await session.flush()
        return participant
    if haigo is not None:
        participant.haigo = haigo
    return participant


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
        KukaiParticipant.kukai_id == kukai_id,
        KukaiParticipant.haigo.is_not(None),
        func.lower(func.trim(KukaiParticipant.haigo)) == normalized,
    )
    if exclude_user_id is not None:
        q = q.where(KukaiParticipant.user_id != exclude_user_id)

    result = await session.execute(q)
    return result.scalar_one() > 0
