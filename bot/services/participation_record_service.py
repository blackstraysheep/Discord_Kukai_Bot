"""Participation-history query service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.entry import Entry
from bot.models.guild_settings import GuildSettings
from bot.models.kukai import Kukai
from bot.models.participant import KukaiParticipant
from bot.models.select import OverallSelectComment, Select
from bot.models.submission import Submission
from bot.services import result_service
from bot.services.errors import PermissionError
from bot.state_machine.states import KukaiState

RecordScope = Literal["current", "all"]
RecordGroupBy = Literal["kukai", "server", "haigo"]


@dataclass(frozen=True)
class ParticipationSubmission:
    text: str
    total_score: int | None = None


@dataclass(frozen=True)
class ParticipationSelection:
    selected_text: str
    author_name: str | None
    comment: str | None = None


@dataclass(frozen=True)
class ParticipationSelectionGroup:
    label: str
    selections: list[ParticipationSelection] = field(default_factory=list)


@dataclass(frozen=True)
class ParticipationRecord:
    kukai_id: int
    guild_id: int
    channel_id: int | None
    result_message_id: int | None
    title: str
    title_url: str | None
    state: str
    participant_haigo: str | None
    participant_display_name: str
    submissions: list[ParticipationSubmission] = field(default_factory=list)
    selections_by_label: list[ParticipationSelectionGroup] = field(default_factory=list)
    overall_comment: str | None = None


@dataclass(frozen=True)
class ParticipationRecordResult:
    target_user_id: int
    target_display_name: str
    scope: RecordScope
    group_by: RecordGroupBy
    records: list[ParticipationRecord]
    total_kukai_count: int
    submission_count: int
    selection_count: int
    overall_comment_count: int


async def get_participation_records(
    session: AsyncSession,
    *,
    current_guild_id: int,
    target_user_id: int,
    target_display_name: str,
    viewer_user_id: int,
    scope: RecordScope = "current",
    group_by: RecordGroupBy = "kukai",
    haigo: str | None = None,
) -> ParticipationRecordResult:
    is_self = target_user_id == viewer_user_id
    if not is_self:
        await _ensure_other_record_visible(session, current_guild_id)
        scope = "current"

    kukais = await _load_target_kukais(
        session,
        target_user_id=target_user_id,
        current_guild_id=current_guild_id,
        scope=scope,
        is_self=is_self,
    )
    records: list[ParticipationRecord] = []
    for kukai in kukais:
        record = await _build_record(
            session,
            kukai,
            target_user_id=target_user_id,
            target_display_name=target_display_name,
        )
        if haigo is not None and record.participant_haigo != haigo:
            continue
        records.append(record)

    return ParticipationRecordResult(
        target_user_id=target_user_id,
        target_display_name=target_display_name,
        scope=scope,
        group_by=group_by,
        records=records,
        total_kukai_count=len(records),
        submission_count=sum(len(record.submissions) for record in records),
        selection_count=sum(
            len(group.selections) for record in records for group in record.selections_by_label
        ),
        overall_comment_count=sum(1 for record in records if record.overall_comment),
    )


async def _ensure_other_record_visible(session: AsyncSession, guild_id: int) -> None:
    settings = await session.get(GuildSettings, guild_id)
    visibility = (
        settings.participation_record_visibility if settings is not None else "private"
    )
    if visibility != "guild_public":
        raise PermissionError("このサーバーでは他人の参加記録閲覧は許可されていません。")


async def _load_target_kukais(
    session: AsyncSession,
    *,
    target_user_id: int,
    current_guild_id: int,
    scope: RecordScope,
    is_self: bool,
) -> list[Kukai]:
    kukai_ids: set[int] = set()
    for model, column in (
        (Entry, Entry.user_id),
        (Submission, Submission.user_id),
        (Select, Select.selector_user_id),
        (OverallSelectComment, OverallSelectComment.user_id),
    ):
        stmt = select(model.kukai_id).join(Kukai, Kukai.id == model.kukai_id).where(column == target_user_id)
        if scope == "current":
            stmt = stmt.where(Kukai.guild_id == current_guild_id)
        if not is_self:
            stmt = stmt.where(Kukai.guild_id == current_guild_id, Kukai.state.in_([
                KukaiState.RESULTS.value,
                KukaiState.ENDED.value,
            ]))
        result = await session.execute(stmt)
        kukai_ids.update(result.scalars().all())

    if not kukai_ids:
        return []

    result = await session.execute(
        select(Kukai)
        .where(Kukai.id.in_(kukai_ids))
        .order_by(Kukai.created_at.desc(), Kukai.id.desc())
        .options(selectinload(Kukai.entries), selectinload(Kukai.participants))
    )
    return list(result.scalars().all())


async def _build_record(
    session: AsyncSession,
    kukai: Kukai,
    *,
    target_user_id: int,
    target_display_name: str,
) -> ParticipationRecord:
    participant_haigo = _participant_haigo(kukai, target_user_id)
    participant_display_name = participant_haigo or target_display_name or f"UID:{target_user_id}"

    submissions = await _load_submissions(session, kukai, target_user_id)
    selections_by_label = await _load_selections(session, kukai, target_user_id)
    overall_comment = await _load_overall_comment(session, kukai.id, target_user_id)

    return ParticipationRecord(
        kukai_id=kukai.id,
        guild_id=kukai.guild_id,
        channel_id=kukai.channel_id,
        result_message_id=kukai.result_message_id,
        title=kukai.title,
        title_url=_title_url(kukai),
        state=kukai.state,
        participant_haigo=participant_haigo,
        participant_display_name=participant_display_name,
        submissions=submissions,
        selections_by_label=selections_by_label,
        overall_comment=overall_comment,
    )


def _participant_haigo(kukai: Kukai, user_id: int) -> str | None:
    for entry in kukai.entries:
        if entry.user_id == user_id and entry.haigo:
            return entry.haigo
    for participant in kukai.participants:
        if participant.user_id == user_id and participant.haigo:
            return participant.haigo
    return None


async def _load_submissions(
    session: AsyncSession,
    kukai: Kukai,
    user_id: int,
) -> list[ParticipationSubmission]:
    result = await session.execute(
        select(Submission)
        .where(
            Submission.kukai_id == kukai.id,
            Submission.user_id == user_id,
            Submission.is_discarded.is_(False),
        )
        .order_by(Submission.created_at, Submission.id)
    )
    submissions = list(result.scalars().all())
    score_by_text: dict[str, int] = {}
    if _can_show_scores(kukai):
        try:
            score_by_text = {
                row.text: row.total_score for row in await result_service.compute_results(session, kukai)
            }
        except Exception:
            score_by_text = {}
    return [
        ParticipationSubmission(text=row.text, total_score=score_by_text.get(row.text))
        for row in submissions
    ]


async def _load_selections(
    session: AsyncSession,
    kukai: Kukai,
    user_id: int,
) -> list[ParticipationSelectionGroup]:
    result = await session.execute(
        select(Select)
        .where(Select.kukai_id == kukai.id, Select.selector_user_id == user_id)
        .options(
            selectinload(Select.submission),
            selectinload(Select.select_label),
            selectinload(Select.comment),
        )
    )
    selects = list(result.scalars().all())
    author_names = await _author_name_map(session, kukai)
    grouped: dict[str, list[ParticipationSelection]] = {}
    label_order: dict[str, int] = {}
    for row in selects:
        label = row.select_label.label if row.select_label is not None else "選句"
        label_order.setdefault(label, row.select_label.rank_priority if row.select_label else 999)
        grouped.setdefault(label, []).append(
            ParticipationSelection(
                selected_text=row.submission.text if row.submission is not None else "",
                author_name=author_names.get(row.submission.user_id) if row.submission is not None else None,
                comment=row.comment.comment if row.comment is not None else None,
            )
        )
    return [
        ParticipationSelectionGroup(label=label, selections=grouped[label])
        for label in sorted(grouped, key=lambda item: label_order[item])
    ]


async def _load_overall_comment(
    session: AsyncSession,
    kukai_id: int,
    user_id: int,
) -> str | None:
    result = await session.execute(
        select(OverallSelectComment.comment).where(
            OverallSelectComment.kukai_id == kukai_id,
            OverallSelectComment.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _author_name_map(session: AsyncSession, kukai: Kukai) -> dict[int, str | None]:
    result = await session.execute(
        select(Submission)
        .where(Submission.kukai_id == kukai.id, Submission.is_discarded.is_(False))
    )
    submissions = list(result.scalars().all())
    if not kukai.author_reveal or KukaiState.from_value(kukai.state) not in {
        KukaiState.RESULTS,
        KukaiState.ENDED,
    }:
        return {row.user_id: None for row in submissions}

    score_by_author: dict[int, int] = {}
    if not kukai.author_reveal_zero:
        try:
            for row in await result_service.compute_results(session, kukai):
                score_by_author[row.author_user_id] = row.total_score
        except Exception:
            score_by_author = {}

    names: dict[int, str | None] = {}
    for submission in submissions:
        if not kukai.author_reveal_zero and score_by_author.get(submission.user_id, 0) <= 0:
            names[submission.user_id] = None
            continue
        names[submission.user_id] = _participant_haigo(kukai, submission.user_id) or f"UID:{submission.user_id}"
    return names


def _can_show_scores(kukai: Kukai) -> bool:
    if not kukai.points_enabled:
        return False
    return KukaiState.from_value(kukai.state) in {
        KukaiState.SELECTING_CLOSED,
        KukaiState.RESULTS,
        KukaiState.ENDED,
    }


def _title_url(kukai: Kukai) -> str | None:
    if kukai.channel_id is None:
        return None
    if kukai.result_message_id is not None:
        return f"https://discord.com/channels/{kukai.guild_id}/{kukai.channel_id}/{kukai.result_message_id}"
    return f"https://discord.com/channels/{kukai.guild_id}/{kukai.channel_id}"
