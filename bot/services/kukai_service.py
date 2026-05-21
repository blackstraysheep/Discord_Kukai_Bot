"""Kukai CRUD and lifecycle operations."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.kukai import Kukai, KukaiAdmin
from bot.models.select_rule import SelectLabel
from bot.repositories import kukai_repo
from bot.services import select_rule_service
from bot.services.errors import (
    DeadlineConflictError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from bot.state_machine.machine import StateMachine
from bot.state_machine.states import KukaiState
from bot.state_machine.transitions import next_state

# Shared state machine instance (no callbacks yet; added in later phases)
_state_machine = StateMachine()
logger = logging.getLogger(__name__)

_SUBMISSION_LOCKED_STATES = {
    KukaiState.SELECTING_OPEN,
    KukaiState.SELECTING_CLOSED,
    KukaiState.RESULTS,
    KukaiState.ENDED,
    KukaiState.CANCELLED,
}

_VALID_SUBMISSION_MODES = {"manual", "semi_auto", "full_auto"}
_VALID_ENTRY_MODES = {"manual", "full_auto"}
_VALID_SELECTING_MODES = {"manual", "semi_auto", "full_auto"}
_VALID_PUBLISH_MODES = {"manual", "auto"}
_VALID_RESULT_MODES = {"manual", "auto"}


async def create_kukai(
    session: AsyncSession,
    *,
    guild_id: int,
    created_by: int,
    channel_id: int,
    title: str,
    theme: str | None = None,
    description: str | None = None,
    entry_close_at: datetime | None = None,
    submission_close_at: datetime | None = None,
    selecting_close_at: datetime | None = None,
    # Optional settings (wizard-provided overrides)
    entry_enabled: bool = True,
    entry_approval: bool = False,
    entry_mode: str = "manual",
    min_participants: int = 0,
    submission_min: int = 1,
    submission_max: int | None = 5,
    submission_mode: str = "manual",
    selecting_mode: str = "manual",
    submission_overflow: bool = False,
    points_enabled: bool = True,
    publish_mode: str = "manual",
    result_mode: str = "manual",
    author_reveal: bool = True,
    author_reveal_zero: bool = True,
    select_label_specs: list[dict] | None = None,
) -> Kukai:
    if not entry_enabled:
        entry_approval = False
        entry_mode = "manual"
    if not author_reveal:
        author_reveal_zero = True

    if entry_mode not in _VALID_ENTRY_MODES:
        raise ValidationError("entry_mode は manual/full_auto で指定してください。")
    _validate_deadlines(entry_close_at, submission_close_at, selecting_close_at)
    _validate_future_deadlines(entry_close_at, submission_close_at, selecting_close_at)

    initial_state = KukaiState.ENTRY_OPEN if entry_enabled else KukaiState.DRAFT

    kukai = Kukai(
        guild_id=guild_id,
        created_by=created_by,
        channel_id=channel_id,
        title=title,
        theme=theme,
        description=description,
        state=initial_state,
        entry_close_at=entry_close_at,
        submission_close_at=submission_close_at,
        selecting_close_at=selecting_close_at,
        entry_enabled=entry_enabled,
        entry_approval=entry_approval,
        entry_mode=entry_mode,
        min_participants=min_participants,
        submission_min=submission_min,
        submission_max=submission_max,
        submission_mode=submission_mode,
        selecting_mode=selecting_mode,
        submission_overflow=submission_overflow,
        publish_mode=publish_mode,
        result_mode=result_mode,
        points_enabled=points_enabled,
        author_reveal=author_reveal,
        author_reveal_zero=author_reveal_zero,
    )
    session.add(kukai)
    await session.flush()  # obtain id

    if select_label_specs is None:
        select_label_specs = select_rule_service.default_kukai_specs()
    normalized_specs = select_rule_service.normalize_kukai_specs(select_label_specs)

    for data in normalized_specs:
        session.add(SelectLabel(kukai_id=kukai.id, **data))
    await session.flush()
    await session.refresh(kukai, attribute_names=["select_labels"])

    logger.info(
        "event=create_kukai kukai_id=%s title=%r entry_enabled=%s initial_state=%s "
        "created_by=%s channel_id=%s",
        kukai.id,
        kukai.title,
        kukai.entry_enabled,
        kukai.state,
        kukai.created_by,
        kukai.channel_id,
    )

    return kukai


async def get_kukai(
    session: AsyncSession, kukai_id: int, guild_id: int
) -> Kukai:
    kukai = await session.get(Kukai, kukai_id)
    if kukai is None or kukai.guild_id != guild_id:
        raise NotFoundError(f"句会 ID {kukai_id} が見つかりません。")
    return kukai


async def resolve_kukai_in_channel(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int | None,
    kukai_id: int | None = None,
) -> Kukai:
    """Resolve kukai by explicit ID or by current channel when unambiguous."""
    if kukai_id is not None:
        return await get_kukai(session, kukai_id, guild_id)

    if channel_id is None:
        raise ValidationError("この場所では句会を自動特定できません。kukai_id を指定してください。")

    result = await session.execute(
        select(Kukai)
        .where(Kukai.guild_id == guild_id, Kukai.channel_id == channel_id)
        .where(Kukai.state.notin_([KukaiState.ENDED, KukaiState.CANCELLED]))
        .order_by(Kukai.created_at.desc())
    )
    rows = list(result.scalars().all())
    if not rows:
        raise NotFoundError("このチャンネルに進行中の句会がありません。kukai_id を指定してください。")
    if len(rows) > 1:
        raise ValidationError("このチャンネルには複数句会があります。kukai_id を指定してください。")
    return rows[0]


async def list_kukais(session: AsyncSession, guild_id: int) -> list[Kukai]:
    """Return listed kukais for a guild, newest first.

    RESULTS is treated as already-finished for `/kukai list`.
    """
    result = await session.execute(
        select(Kukai)
        .where(Kukai.guild_id == guild_id)
        .where(Kukai.state.notin_([KukaiState.RESULTS, KukaiState.ENDED, KukaiState.CANCELLED]))
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


async def add_kukai_admin(
    session: AsyncSession,
    kukai: Kukai,
    *,
    user_id: int,
    added_by: int,
) -> KukaiAdmin:
    if user_id == kukai.created_by:
        raise ValidationError("作成者はすでに句会管理者です。")

    if await kukai_repo.is_admin(session, kukai.id, user_id):
        raise ValidationError("すでに句会管理者に追加されています。")

    return await kukai_repo.add_admin(session, kukai.id, user_id, added_by)


async def remove_kukai_admin(
    session: AsyncSession,
    kukai: Kukai,
    *,
    user_id: int,
) -> None:
    if user_id == kukai.created_by:
        raise ValidationError("作成者は句会管理者から削除できません。")

    removed = await kukai_repo.remove_admin(session, kukai.id, user_id)
    if not removed:
        raise NotFoundError("指定ユーザーは句会管理者ではありません。")


async def edit_kukai(
    session: AsyncSession,
    kukai: Kukai,
    *,
    title: str | None = None,
    theme: str | None = None,
    description: str | None = None,
    submission_close_at: datetime | None = None,
    selecting_close_at: datetime | None = None,
    entry_mode: str | None = None,
    submission_min: int | None = None,
    submission_max: int | None = None,
    submission_max_unlimited: bool = False,
    submission_mode: str | None = None,
    selecting_mode: str | None = None,
    publish_mode: str | None = None,
    result_mode: str | None = None,
    author_reveal: bool | None = None,
    author_reveal_zero: bool | None = None,
) -> bool:
    """Edit kukai fields and return True when deadline fields changed."""
    state = KukaiState.from_value(kukai.state)

    submission_setting_change = (
        submission_max_unlimited
        or any(
            value is not None
            for value in (
                submission_min,
                submission_max,
                entry_mode,
                submission_mode,
                selecting_mode,
            )
        )
    )
    if submission_setting_change and state in _SUBMISSION_LOCKED_STATES:
        raise InvalidStateError("この状態では投句設定を変更できません。")

    if title is not None:
        title = title.strip()
        if not title:
            raise ValidationError("title は空にできません。")
        kukai.title = title

    if theme is not None:
        theme = theme.strip()
        kukai.theme = theme or None

    if description is not None:
        description = description.strip()
        kukai.description = description or None

    if submission_mode is not None:
        if submission_mode not in _VALID_SUBMISSION_MODES:
            raise ValidationError("submission_mode は manual/semi_auto/full_auto で指定してください。")
        kukai.submission_mode = submission_mode
    if selecting_mode is not None:
        if selecting_mode not in _VALID_SELECTING_MODES:
            raise ValidationError("selecting_mode は manual/semi_auto/full_auto で指定してください。")
        kukai.selecting_mode = selecting_mode
    if entry_mode is not None:
        if entry_mode not in _VALID_ENTRY_MODES:
            raise ValidationError("entry_mode は manual/full_auto で指定してください。")
        kukai.entry_mode = entry_mode

    if publish_mode is not None:
        if publish_mode not in _VALID_PUBLISH_MODES:
            raise ValidationError("publish_mode は manual/auto で指定してください。")
        kukai.publish_mode = publish_mode

    if result_mode is not None:
        if result_mode not in _VALID_RESULT_MODES:
            raise ValidationError("result_mode は manual/auto で指定してください。")
        kukai.result_mode = result_mode

    if author_reveal is not None:
        kukai.author_reveal = author_reveal
        if not author_reveal:
            kukai.author_reveal_zero = True
    if author_reveal_zero is not None:
        kukai.author_reveal_zero = author_reveal_zero

    if not kukai.entry_enabled:
        kukai.entry_approval = False
        kukai.entry_mode = "manual"
    if not kukai.author_reveal:
        kukai.author_reveal_zero = True

    if submission_max_unlimited and submission_max is not None:
        raise ValidationError("submission_max と submission_max_unlimited は同時指定できません。")

    effective_submission_min = submission_min if submission_min is not None else kukai.submission_min
    effective_submission_max = (
        None
        if submission_max_unlimited
        else (submission_max if submission_max is not None else kukai.submission_max)
    )
    if effective_submission_min < 1:
        raise ValidationError("submission_min は1以上にしてください。")
    if effective_submission_max is not None and effective_submission_max < effective_submission_min:
        raise ValidationError("submission_max は submission_min 以上にしてください。")

    if submission_min is not None:
        kukai.submission_min = submission_min
    if submission_max_unlimited:
        kukai.submission_max = None
    elif submission_max is not None:
        kukai.submission_max = submission_max

    old_submission_close_at = kukai.submission_close_at
    old_selecting_close_at = kukai.selecting_close_at

    new_submission_close_at = (
        submission_close_at if submission_close_at is not None else kukai.submission_close_at
    )
    new_selecting_close_at = selecting_close_at if selecting_close_at is not None else kukai.selecting_close_at
    _validate_deadlines(None, new_submission_close_at, new_selecting_close_at)
    _validate_future_deadlines(None, submission_close_at, selecting_close_at)

    if submission_close_at is not None:
        kukai.submission_close_at = submission_close_at
    if selecting_close_at is not None:
        kukai.selecting_close_at = selecting_close_at

    return (
        submission_close_at is not None
        and submission_close_at != old_submission_close_at
    ) or (
        selecting_close_at is not None
        and selecting_close_at != old_selecting_close_at
    )


async def update_deadlines(
    session: AsyncSession,
    kukai: Kukai,
    submission_close_at: datetime | None,
    selecting_close_at: datetime | None,
) -> None:
    _validate_deadlines(None, submission_close_at, selecting_close_at)
    _validate_future_deadlines(None, submission_close_at, selecting_close_at)
    if submission_close_at is not None:
        kukai.submission_close_at = submission_close_at
    if selecting_close_at is not None:
        kukai.selecting_close_at = selecting_close_at


def _validate_deadlines(
    entry_close_at: datetime | None,
    submission_close_at: datetime | None,
    selecting_close_at: datetime | None,
) -> None:
    if (
        entry_close_at is not None
        and submission_close_at is not None
            and submission_close_at < entry_close_at
    ):
        raise DeadlineConflictError("投句締切はエントリー締切以降に設定してください。")
    if (
        submission_close_at is not None
        and selecting_close_at is not None
        and selecting_close_at <= submission_close_at
    ):
        raise DeadlineConflictError("選句締切は投句締切より後に設定してください。")


def _validate_future_deadlines(*deadlines: datetime | None) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for deadline in deadlines:
        if deadline is not None and deadline <= now:
            raise DeadlineConflictError("締切は現在時刻より未来に設定してください。")
