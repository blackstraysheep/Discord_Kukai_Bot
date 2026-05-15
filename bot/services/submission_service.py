"""Submission lifecycle operations."""

import random
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.submission import PublishedSubmission, Submission
from bot.models.select import OverallSelectComment, Select, SelectComment
from bot.repositories import entry_repo, submission_repo
from bot.services.errors import InvalidStateError, NotFoundError, ValidationError
from bot.state_machine.states import KukaiState
from bot.utils.text import normalize

_SUBMISSION_OPEN = KukaiState.SUBMISSION_OPEN
ROLLBACK_STATE_ORDER: tuple[KukaiState, ...] = (
    KukaiState.DRAFT,
    KukaiState.ENTRY_OPEN,
    KukaiState.ENTRY_CLOSED,
    KukaiState.SUBMISSION_OPEN,
    KukaiState.SUBMISSION_CLOSED,
    KukaiState.WAITING_PUBLISH,
    KukaiState.SELECTING_OPEN,
    KukaiState.SELECTING_CLOSED,
    KukaiState.RESULTS,
)
_ROLLBACK_STATE_INDEX = {state: index for index, state in enumerate(ROLLBACK_STATE_ORDER)}


async def submit(
    session: AsyncSession,
    kukai,
    user_id: int,
    text: str,
) -> tuple[Submission, bool]:
    """Register a haiku. Returns (submission, over_limit_warning)."""
    if KukaiState.from_value(kukai.state) != _SUBMISSION_OPEN:
        raise InvalidStateError("現在投句を受け付けていません。")

    text = normalize(text.strip())
    if not text:
        raise ValidationError("俳句の本文が空です。")

    if kukai.entry_enabled:
        entry = await entry_repo.get_by_user(session, kukai.id, user_id)
        if not entry or entry.status != "approved":
            raise InvalidStateError("この句会への参加登録（承認済み）が必要です。")

    current_count = await submission_repo.count_user_submissions(session, kukai.id, user_id)
    over_limit = False
    if kukai.submission_max is not None and current_count >= kukai.submission_max:
        if not kukai.submission_overflow:
            raise ValidationError(f"投句数の上限（{kukai.submission_max}句）に達しています。")
        over_limit = True

    sub = Submission(kukai_id=kukai.id, user_id=user_id, text=text)
    session.add(sub)
    await session.flush()
    return sub, over_limit


async def edit(
    session: AsyncSession,
    kukai,
    user_id: int,
    submission_id: int,
    new_text: str,
) -> Submission:
    if KukaiState.from_value(kukai.state) != _SUBMISSION_OPEN:
        raise InvalidStateError("現在投句を受け付けていません。")

    new_text = normalize(new_text.strip())
    if not new_text:
        raise ValidationError("俳句の本文が空です。")

    sub = await submission_repo.get(session, submission_id)
    if not sub or sub.kukai_id != kukai.id or sub.user_id != user_id or sub.is_discarded:
        raise NotFoundError("該当する投句が見つかりません。")

    sub.text = new_text
    return sub


async def delete_submission(
    session: AsyncSession,
    kukai,
    user_id: int,
    submission_id: int,
) -> None:
    if KukaiState.from_value(kukai.state) != _SUBMISSION_OPEN:
        raise InvalidStateError("現在投句を受け付けていません。")

    sub = await submission_repo.get(session, submission_id)
    if not sub or sub.kukai_id != kukai.id or sub.user_id != user_id or sub.is_discarded:
        raise NotFoundError("該当する投句が見つかりません。")

    await session.delete(sub)


async def list_user_submissions(
    session: AsyncSession, kukai_id: int, user_id: int
) -> list[Submission]:
    return await submission_repo.get_user_submissions(session, kukai_id, user_id)


async def publish(
    session: AsyncSession,
    kukai,
) -> list[PublishedSubmission]:
    """Assign random display numbers. State must be WAITING_PUBLISH."""
    if KukaiState.from_value(kukai.state) != KukaiState.WAITING_PUBLISH:
        raise InvalidStateError("投句公開は「投句公開待ち」状態でのみ実行できます。")

    if kukai.submission_incomplete == "discard":
        all_subs = await submission_repo.list_by_kukai(session, kukai.id)
        by_user: dict[int, list[Submission]] = defaultdict(list)
        for sub in all_subs:
            by_user[sub.user_id].append(sub)
        for subs in by_user.values():
            if len(subs) < kukai.submission_min:
                for sub in subs:
                    sub.is_discarded = True
        await session.flush()

    to_publish = await submission_repo.list_by_kukai(session, kukai.id)
    if not to_publish:
        raise ValidationError("公開対象の投句がありません。")

    numbers = list(range(1, len(to_publish) + 1))
    random.shuffle(numbers)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    published: list[PublishedSubmission] = []
    for sub, num in zip(to_publish, numbers):
        ps = PublishedSubmission(
            kukai_id=kukai.id,
            submission_id=sub.id,
            number=num,
            published_at=now,
        )
        ps.submission = sub  # attach to avoid lazy-load
        session.add(ps)
        published.append(ps)

    await session.flush()
    published.sort(key=lambda x: x.number)
    return published


async def rollback_publish(
    session: AsyncSession,
    kukai,
    *,
    reset_selects: bool = False,
) -> None:
    """Undo publish: delete PublishedSubmission rows and optionally all selects."""
    if KukaiState.from_value(kukai.state) == KukaiState.WAITING_PUBLISH:
        await submission_repo.restore_discarded(session, kukai.id)
        await submission_repo.delete_published(session, kukai.id)
        if reset_selects:
            await _delete_selects(session, kukai.id)
        await session.flush()
        return

    await rollback_to_state(
        session,
        kukai,
        KukaiState.WAITING_PUBLISH,
        keep_submissions=True,
        keep_selects=not reset_selects,
    )


def can_reset_submissions_on_rollback(target: KukaiState) -> bool:
    target = KukaiState.from_value(target)
    return _rollback_index(target) <= _rollback_index(KukaiState.SUBMISSION_OPEN)


def validate_rollback_target(
    current: KukaiState,
    target: KukaiState,
    *,
    keep_submissions: bool = True,
) -> None:
    current = KukaiState.from_value(current)
    target = KukaiState.from_value(target)

    if current in {KukaiState.PAUSED, *KukaiState.terminal_states()}:
        raise InvalidStateError("この状態ではロールバックできません。")
    if current not in _ROLLBACK_STATE_INDEX:
        raise InvalidStateError("この状態ではロールバックできません。")
    if target not in _ROLLBACK_STATE_INDEX:
        raise InvalidStateError("指定された状態へはロールバックできません。")
    if _rollback_index(target) >= _rollback_index(current):
        raise InvalidStateError("現在より前の状態を指定してください。")
    if not keep_submissions and not can_reset_submissions_on_rollback(target):
        raise InvalidStateError("投句をリセットする場合は「投句受付中」以前に戻してください。")


async def rollback_to_state(
    session: AsyncSession,
    kukai,
    target: KukaiState,
    *,
    keep_submissions: bool = True,
    keep_selects: bool = True,
) -> None:
    """Rollback kukai to a previous stage and optionally discard submission/select data."""
    current = KukaiState.from_value(kukai.state)
    target = KukaiState.from_value(target)
    if not keep_submissions:
        keep_selects = False
    validate_rollback_target(current, target, keep_submissions=keep_submissions)

    if not keep_selects:
        await _delete_selects(session, kukai.id)

    if keep_submissions:
        if _rollback_index(target) <= _rollback_index(KukaiState.WAITING_PUBLISH):
            await submission_repo.restore_discarded(session, kukai.id)
            await submission_repo.delete_published(session, kukai.id)
    else:
        await submission_repo.delete_published(session, kukai.id)
        await session.execute(delete(Submission).where(Submission.kukai_id == kukai.id))

    kukai.state = target
    kukai.pre_pause_state = None
    kukai.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.flush()


def _rollback_index(state: KukaiState) -> int:
    return _ROLLBACK_STATE_INDEX[KukaiState.from_value(state)]


async def _delete_selects(session: AsyncSession, kukai_id: int) -> None:
    select_ids = select(Select.id).where(Select.kukai_id == kukai_id)
    await session.execute(delete(SelectComment).where(SelectComment.select_id.in_(select_ids)))
    await session.execute(delete(Select).where(Select.kukai_id == kukai_id))
    await session.execute(delete(OverallSelectComment).where(OverallSelectComment.kukai_id == kukai_id))
