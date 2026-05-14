from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.select import OverallSelectComment, Select, SelectComment


async def get_select(
    session: AsyncSession, kukai_id: int, selector_user_id: int, submission_id: int
) -> Select | None:
    result = await session.execute(
        select(Select)
        .where(
            Select.kukai_id == kukai_id,
            Select.selector_user_id == selector_user_id,
            Select.submission_id == submission_id,
        )
        .options(selectinload(Select.comment), selectinload(Select.select_label))
    )
    return result.scalar_one_or_none()


async def get_selects_by_selector(
    session: AsyncSession, kukai_id: int, selector_user_id: int
) -> list[Select]:
    result = await session.execute(
        select(Select)
        .where(Select.kukai_id == kukai_id, Select.selector_user_id == selector_user_id)
        .options(selectinload(Select.comment), selectinload(Select.select_label))
    )
    return list(result.scalars().all())


async def get_selects_for_submission(
    session: AsyncSession, submission_id: int
) -> list[Select]:
    result = await session.execute(
        select(Select)
        .where(Select.submission_id == submission_id)
        .options(selectinload(Select.comment), selectinload(Select.select_label))
    )
    return list(result.scalars().all())


async def get_all_selects(
    session: AsyncSession, kukai_id: int
) -> list[Select]:
    result = await session.execute(
        select(Select)
        .where(Select.kukai_id == kukai_id)
        .options(selectinload(Select.comment), selectinload(Select.select_label))
    )
    return list(result.scalars().all())


async def count_label_usage(
    session: AsyncSession, kukai_id: int, selector_user_id: int, select_label_id: int
) -> int:
    result = await session.execute(
        select(func.count()).where(
            Select.kukai_id == kukai_id,
            Select.selector_user_id == selector_user_id,
            Select.select_label_id == select_label_id,
        )
    )
    return result.scalar_one()


async def get_overall_comment(
    session: AsyncSession, kukai_id: int, user_id: int
) -> OverallSelectComment | None:
    result = await session.execute(
        select(OverallSelectComment).where(
            OverallSelectComment.kukai_id == kukai_id,
            OverallSelectComment.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_overall_comments(
    session: AsyncSession, kukai_id: int
) -> list[OverallSelectComment]:
    result = await session.execute(
        select(OverallSelectComment).where(OverallSelectComment.kukai_id == kukai_id)
    )
    return list(result.scalars().all())
