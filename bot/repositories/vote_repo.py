from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.vote import OverallComment, Vote, VoteComment


async def get_vote(
    session: AsyncSession, kukai_id: int, voter_user_id: int, submission_id: int
) -> Vote | None:
    result = await session.execute(
        select(Vote)
        .where(
            Vote.kukai_id == kukai_id,
            Vote.voter_user_id == voter_user_id,
            Vote.submission_id == submission_id,
        )
        .options(selectinload(Vote.comment), selectinload(Vote.vote_label))
    )
    return result.scalar_one_or_none()


async def get_votes_by_voter(
    session: AsyncSession, kukai_id: int, voter_user_id: int
) -> list[Vote]:
    result = await session.execute(
        select(Vote)
        .where(Vote.kukai_id == kukai_id, Vote.voter_user_id == voter_user_id)
        .options(selectinload(Vote.comment), selectinload(Vote.vote_label))
    )
    return list(result.scalars().all())


async def get_votes_for_submission(
    session: AsyncSession, submission_id: int
) -> list[Vote]:
    result = await session.execute(
        select(Vote)
        .where(Vote.submission_id == submission_id)
        .options(selectinload(Vote.comment), selectinload(Vote.vote_label))
    )
    return list(result.scalars().all())


async def get_all_votes(
    session: AsyncSession, kukai_id: int
) -> list[Vote]:
    result = await session.execute(
        select(Vote)
        .where(Vote.kukai_id == kukai_id)
        .options(selectinload(Vote.comment), selectinload(Vote.vote_label))
    )
    return list(result.scalars().all())


async def count_label_usage(
    session: AsyncSession, kukai_id: int, voter_user_id: int, vote_label_id: int
) -> int:
    result = await session.execute(
        select(func.count()).where(
            Vote.kukai_id == kukai_id,
            Vote.voter_user_id == voter_user_id,
            Vote.vote_label_id == vote_label_id,
        )
    )
    return result.scalar_one()


async def get_overall_comment(
    session: AsyncSession, kukai_id: int, user_id: int
) -> OverallComment | None:
    result = await session.execute(
        select(OverallComment).where(
            OverallComment.kukai_id == kukai_id,
            OverallComment.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_overall_comments(
    session: AsyncSession, kukai_id: int
) -> list[OverallComment]:
    result = await session.execute(
        select(OverallComment).where(OverallComment.kukai_id == kukai_id)
    )
    return list(result.scalars().all())
