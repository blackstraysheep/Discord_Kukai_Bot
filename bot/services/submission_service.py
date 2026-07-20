"""Submission lifecycle operations."""

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.entry import Entry
from bot.models.kukai import Kukai
from bot.models.participant import KukaiParticipant
from bot.models.submission import PublishedSubmission, Submission
from bot.models.select import OverallSelectComment, Select, SelectComment
from bot.repositories import entry_repo, participant_repo, submission_repo
from bot.services.errors import InvalidStateError, NotFoundError, ValidationError
from bot.state_machine.states import KukaiState
from bot.utils.submission_markup import SubmissionMarkupError, validate_submission_markup
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


@dataclass(frozen=True)
class DuplicateSubmissionWarning:
    submission_id: int
    text: str
    kukai_id: int
    title: str
    guild_id: int
    channel_id: int | None
    result_message_id: int | None
    published_number: int | None
    haigo: str | None

    @property
    def title_url(self) -> str | None:
        if self.channel_id is None:
            return None
        if self.result_message_id is not None:
            return (
                f"https://discord.com/channels/{self.guild_id}/"
                f"{self.channel_id}/{self.result_message_id}"
            )
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}"


@dataclass(frozen=True)
class SubmissionResult:
    submission: Submission
    over_limit_warning: bool
    duplicate_warnings: list[DuplicateSubmissionWarning]

    def __iter__(self):
        yield self.submission
        yield self.over_limit_warning


async def submit(
    session: AsyncSession,
    kukai,
    user_id: int,
    text: str,
    *,
    haigo: str | None = None,
) -> SubmissionResult:
    """Register a haiku."""
    if KukaiState.from_value(kukai.state) != _SUBMISSION_OPEN:
        raise InvalidStateError("現在投句を受け付けていません。")

    text = normalize(text.strip())
    if not text:
        raise ValidationError("俳句の本文が空です。")
    _validate_markup(text)

    if kukai.entry_enabled:
        entry = await entry_repo.get_by_user(session, kukai.id, user_id)
        if not entry or entry.status != "approved":
            raise InvalidStateError("この句会への参加登録（承認済み）が必要です。")
    else:
        await _ensure_participant_profile(session, kukai.id, user_id, haigo=haigo)

    current_count = await submission_repo.count_user_submissions(session, kukai.id, user_id)
    over_limit = False
    if kukai.submission_max is not None and current_count >= kukai.submission_max:
        if not kukai.submission_overflow:
            raise ValidationError(f"投句数の上限（{kukai.submission_max}句）に達しています。")
        over_limit = True

    duplicate_warnings = await find_duplicate_submission_warnings(
        session,
        user_id=user_id,
        normalized_text=text,
    )
    sub = Submission(kukai_id=kukai.id, user_id=user_id, text=text)
    session.add(sub)
    await session.flush()
    return SubmissionResult(
        submission=sub,
        over_limit_warning=over_limit,
        duplicate_warnings=duplicate_warnings,
    )


async def find_duplicate_submission_warnings(
    session: AsyncSession,
    *,
    user_id: int,
    normalized_text: str,
    exclude_submission_id: int | None = None,
) -> list[DuplicateSubmissionWarning]:
    stmt = (
        select(Submission)
        .where(
            Submission.user_id == user_id,
            Submission.text == normalized_text,
            Submission.is_discarded.is_(False),
        )
        .options(selectinload(Submission.kukai), selectinload(Submission.published))
        .order_by(Submission.created_at.desc(), Submission.id.desc())
    )
    if exclude_submission_id is not None:
        stmt = stmt.where(Submission.id != exclude_submission_id)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    if not rows:
        return []

    kukai_ids = {row.kukai_id for row in rows}
    entries_result = await session.execute(
        select(Entry).where(Entry.kukai_id.in_(kukai_ids), Entry.user_id == user_id)
    )
    participants_result = await session.execute(
        select(KukaiParticipant).where(
            KukaiParticipant.kukai_id.in_(kukai_ids),
            KukaiParticipant.user_id == user_id,
        )
    )
    entries = {row.kukai_id: row for row in entries_result.scalars().all()}
    participants = {row.kukai_id: row for row in participants_result.scalars().all()}

    warnings: list[DuplicateSubmissionWarning] = []
    seen_submission_ids: set[int] = set()
    for row in rows:
        if row.id in seen_submission_ids:
            continue
        seen_submission_ids.add(row.id)
        kukai: Kukai = row.kukai
        entry = entries.get(row.kukai_id)
        participant = participants.get(row.kukai_id)
        haigo_value = None
        if entry is not None and entry.haigo:
            haigo_value = entry.haigo
        elif participant is not None and participant.haigo:
            haigo_value = participant.haigo
        warnings.append(
            DuplicateSubmissionWarning(
                submission_id=row.id,
                text=row.text,
                kukai_id=kukai.id,
                title=kukai.title,
                guild_id=kukai.guild_id,
                channel_id=kukai.channel_id,
                result_message_id=kukai.result_message_id,
                published_number=row.published.number if row.published is not None else None,
                haigo=haigo_value,
            )
        )
    return warnings


async def get_participant_profile(session: AsyncSession, kukai_id: int, user_id: int):
    return await participant_repo.get_by_user(session, kukai_id, user_id)


async def _ensure_participant_profile(
    session: AsyncSession,
    kukai_id: int,
    user_id: int,
    *,
    haigo: str | None,
) -> None:
    cleaned = haigo.strip() if haigo else None
    if cleaned:
        conflict = await participant_repo.has_haigo_conflict(
            session, kukai_id, cleaned, exclude_user_id=user_id
        )
        if conflict:
            raise ValidationError("その俳号はこの句会ですでに使われています。別の俳号を指定してください。")
        entry_conflict = await entry_repo.has_haigo_conflict(
            session, kukai_id, cleaned, exclude_user_id=user_id
        )
        if entry_conflict:
            raise ValidationError("その俳号はこの句会ですでに使われています。別の俳号を指定してください。")
    await participant_repo.upsert(session, kukai_id, user_id, haigo=cleaned)


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
    _validate_markup(new_text)

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


def _validate_markup(text: str) -> None:
    try:
        validate_submission_markup(text)
    except SubmissionMarkupError as exc:
        raise ValidationError(str(exc)) from exc


async def _delete_selects(session: AsyncSession, kukai_id: int) -> None:
    select_ids = select(Select.id).where(Select.kukai_id == kukai_id)
    await session.execute(delete(SelectComment).where(SelectComment.select_id.in_(select_ids)))
    await session.execute(delete(Select).where(Select.kukai_id == kukai_id))
    await session.execute(delete(OverallSelectComment).where(OverallSelectComment.kukai_id == kukai_id))
