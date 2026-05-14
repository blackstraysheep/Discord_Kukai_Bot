"""Submission lifecycle operations."""

import random
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.submission import PublishedSubmission, Submission
from bot.models.vote import OverallComment, Vote
from bot.repositories import entry_repo, submission_repo
from bot.services.errors import InvalidStateError, NotFoundError, ValidationError
from bot.state_machine.states import KukaiState
from bot.utils.text import normalize

_SUBMISSION_OPEN = KukaiState.SUBMISSION_OPEN
_ROLLBACK_ALLOWED = {KukaiState.WAITING_PUBLISH, KukaiState.VOTING_OPEN, KukaiState.VOTING_CLOSED}


async def submit(
    session: AsyncSession,
    kukai,
    user_id: int,
    text: str,
) -> tuple[Submission, bool]:
    """Register a haiku. Returns (submission, over_limit_warning)."""
    if KukaiState(kukai.state) != _SUBMISSION_OPEN:
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
    if current_count >= kukai.submission_max:
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
    if KukaiState(kukai.state) != _SUBMISSION_OPEN:
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
    if KukaiState(kukai.state) != _SUBMISSION_OPEN:
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
    if KukaiState(kukai.state) != KukaiState.WAITING_PUBLISH:
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
    reset_votes: bool = False,
) -> None:
    """Undo publish: delete PublishedSubmission rows and optionally all votes."""
    if KukaiState(kukai.state) not in _ROLLBACK_ALLOWED:
        raise InvalidStateError("この状態ではロールバックできません。")

    await submission_repo.restore_discarded(session, kukai.id)
    await submission_repo.delete_published(session, kukai.id)

    if reset_votes:
        await session.execute(delete(Vote).where(Vote.kukai_id == kukai.id))
        await session.execute(delete(OverallComment).where(OverallComment.kukai_id == kukai.id))
