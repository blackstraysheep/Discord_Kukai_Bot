from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.submission import PublishedSubmission, Submission


async def get(session: AsyncSession, submission_id: int) -> Submission | None:
    return await session.get(Submission, submission_id)


async def get_user_submissions(
    session: AsyncSession, kukai_id: int, user_id: int
) -> list[Submission]:
    result = await session.execute(
        select(Submission)
        .where(
            Submission.kukai_id == kukai_id,
            Submission.user_id == user_id,
            Submission.is_discarded.is_(False),
        )
        .order_by(Submission.created_at)
    )
    return list(result.scalars().all())


async def count_user_submissions(
    session: AsyncSession, kukai_id: int, user_id: int
) -> int:
    result = await session.execute(
        select(func.count()).where(
            Submission.kukai_id == kukai_id,
            Submission.user_id == user_id,
            Submission.is_discarded.is_(False),
        )
    )
    return result.scalar_one()


async def list_by_kukai(
    session: AsyncSession, kukai_id: int, *, include_discarded: bool = False
) -> list[Submission]:
    q = select(Submission).where(Submission.kukai_id == kukai_id)
    if not include_discarded:
        q = q.where(Submission.is_discarded.is_(False))
    result = await session.execute(q.order_by(Submission.user_id, Submission.created_at))
    return list(result.scalars().all())


async def list_published(
    session: AsyncSession, kukai_id: int
) -> list[PublishedSubmission]:
    result = await session.execute(
        select(PublishedSubmission)
        .where(PublishedSubmission.kukai_id == kukai_id)
        .order_by(PublishedSubmission.number)
        .options(selectinload(PublishedSubmission.submission))
    )
    return list(result.scalars().all())


async def delete_published(session: AsyncSession, kukai_id: int) -> None:
    await session.execute(
        delete(PublishedSubmission).where(PublishedSubmission.kukai_id == kukai_id)
    )


async def restore_discarded(session: AsyncSession, kukai_id: int) -> None:
    await session.execute(
        update(Submission)
        .where(Submission.kukai_id == kukai_id, Submission.is_discarded.is_(True))
        .values(is_discarded=False)
    )
