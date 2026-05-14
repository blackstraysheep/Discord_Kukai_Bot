"""Kukai CRUD and lifecycle operations."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.kukai import Kukai
from bot.models.vote_rule import VoteLabel
from bot.services.errors import DeadlineConflictError, NotFoundError
from bot.state_machine.machine import StateMachine
from bot.state_machine.states import KukaiState
from bot.state_machine.transitions import next_state

# Shared state machine instance (no callbacks yet; added in later phases)
_state_machine = StateMachine()

# Default vote labels copied into every new kukai (requirements §5.1)
_DEFAULT_VOTE_LABELS = [
    {
        "label": "特選",
        "point": 2,
        "rank_priority": 1,
        "display_order": 1,
        "min_count": 0,
        "max_count": 1,
        "comment_mode": "none",
    },
    {
        "label": "並選",
        "point": 1,
        "rank_priority": 2,
        "display_order": 2,
        "min_count": 0,
        "max_count": 5,
        "comment_mode": "none",
    },
    {
        "label": "予選",
        "point": 0,
        "rank_priority": 3,
        "display_order": 3,
        "min_count": 0,
        "max_count": None,
        "comment_mode": "none",
    },
]


async def create_kukai(
    session: AsyncSession,
    *,
    guild_id: int,
    created_by: int,
    channel_id: int,
    title: str,
    theme: str | None = None,
    description: str | None = None,
    submission_close_at: datetime | None = None,
    voting_close_at: datetime | None = None,
    # Optional settings (wizard-provided overrides)
    entry_enabled: bool = True,
    entry_approval: bool = False,
    min_participants: int = 0,
    submission_min: int = 1,
    submission_max: int = 3,
    submission_mode: str = "manual",
    submission_overflow: bool = False,
    publish_mode: str = "manual",
    result_mode: str = "manual",
    author_reveal: bool = True,
) -> Kukai:
    _validate_deadlines(submission_close_at, voting_close_at)

    kukai = Kukai(
        guild_id=guild_id,
        created_by=created_by,
        channel_id=channel_id,
        title=title,
        theme=theme,
        description=description,
        state=KukaiState.DRAFT,
        submission_close_at=submission_close_at,
        voting_close_at=voting_close_at,
        entry_enabled=entry_enabled,
        entry_approval=entry_approval,
        min_participants=min_participants,
        submission_min=submission_min,
        submission_max=submission_max,
        submission_mode=submission_mode,
        submission_overflow=submission_overflow,
        publish_mode=publish_mode,
        result_mode=result_mode,
        author_reveal=author_reveal,
    )
    session.add(kukai)
    await session.flush()  # obtain id

    for data in _DEFAULT_VOTE_LABELS:
        session.add(VoteLabel(kukai_id=kukai.id, **data))
    await session.flush()
    await session.refresh(kukai, attribute_names=["vote_labels"])

    return kukai


async def get_kukai(
    session: AsyncSession, kukai_id: int, guild_id: int
) -> Kukai:
    kukai = await session.get(Kukai, kukai_id)
    if kukai is None or kukai.guild_id != guild_id:
        raise NotFoundError(f"句会 ID {kukai_id} が見つかりません。")
    return kukai


async def list_kukais(session: AsyncSession, guild_id: int) -> list[Kukai]:
    """Return non-terminal kukais for a guild, newest first."""
    result = await session.execute(
        select(Kukai)
        .where(Kukai.guild_id == guild_id)
        .where(Kukai.state.notin_([KukaiState.ENDED, KukaiState.CANCELLED]))
        .order_by(Kukai.created_at.desc())
    )
    return list(result.scalars().all())


async def proceed(session: AsyncSession, kukai: Kukai) -> KukaiState:
    return await _state_machine.proceed(kukai, session, is_admin=True)


async def jump(session: AsyncSession, kukai: Kukai, target: KukaiState) -> None:
    await _state_machine.jump(kukai, target, session)


async def pause(session: AsyncSession, kukai: Kukai) -> None:
    await _state_machine.pause(kukai, session)


async def resume(session: AsyncSession, kukai: Kukai) -> KukaiState:
    return await _state_machine.resume(kukai, session)


async def cancel(session: AsyncSession, kukai: Kukai) -> None:
    await _state_machine.cancel(kukai, session)


async def update_deadlines(
    session: AsyncSession,
    kukai: Kukai,
    submission_close_at: datetime | None,
    voting_close_at: datetime | None,
) -> None:
    _validate_deadlines(submission_close_at, voting_close_at)
    if submission_close_at is not None:
        kukai.submission_close_at = submission_close_at
    if voting_close_at is not None:
        kukai.voting_close_at = voting_close_at


def _validate_deadlines(
    submission_close_at: datetime | None,
    voting_close_at: datetime | None,
) -> None:
    if (
        submission_close_at is not None
        and voting_close_at is not None
        and voting_close_at <= submission_close_at
    ):
        raise DeadlineConflictError("選句締切は投句締切より後に設定してください。")
