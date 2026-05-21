"""Progress requirement checks shared by commands and scheduler jobs."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.entry import Entry
from bot.models.select_rule import SelectLabel
from bot.repositories import select_repo, submission_repo
from bot.services import select_rule_service


@dataclass(frozen=True)
class IncompleteParticipant:
    user_id: int
    display_name: str
    issues: tuple[str, ...]


@dataclass(frozen=True)
class ProgressReport:
    kind: str
    complete: bool
    incomplete: tuple[IncompleteParticipant, ...]
    notes: tuple[str, ...] = ()

    @property
    def has_incomplete(self) -> bool:
        return bool(self.incomplete)

    def admin_lines(self) -> list[str]:
        lines = [f"<@{row.user_id}> {row.display_name}: {', '.join(row.issues)}" for row in self.incomplete]
        lines.extend(self.notes)
        return lines or ["未達はありません。"]

    def summary(self) -> str:
        if self.complete:
            return "条件を満たしています。"
        if self.incomplete:
            return f"{len(self.incomplete)}名が条件を満たしていません。"
        return "条件を満たしていません。"


async def submission_report(session: AsyncSession, kukai) -> ProgressReport:
    """Check whether all known required participants meet submission_min."""
    participants = await _required_participants(session, kukai)
    if participants is None:
        return ProgressReport(
            kind="submission",
            complete=True,
            incomplete=(),
            notes=("エントリー制なしのため、未投句者の自動判定は行いません。",),
        )
    if not participants:
        return ProgressReport(
            kind="submission",
            complete=False,
            incomplete=(),
            notes=("承認済み参加者がいません。",),
        )

    incomplete: list[IncompleteParticipant] = []
    for entry in participants:
        if entry.is_special:
            continue
        count = await submission_repo.count_user_submissions(session, kukai.id, entry.user_id)
        if count < kukai.submission_min:
            incomplete.append(
                IncompleteParticipant(
                    user_id=entry.user_id,
                    display_name=_entry_display_name(entry),
                    issues=(f"投句 {count}/{kukai.submission_min}",),
                )
            )

    return ProgressReport(kind="submission", complete=not incomplete, incomplete=tuple(incomplete))


async def selecting_report(session: AsyncSession, kukai) -> ProgressReport:
    """Check whether all known required participants meet select label minimums."""
    participants = await _required_participants(session, kukai)
    if participants is None:
        return ProgressReport(
            kind="selecting",
            complete=True,
            incomplete=(),
            notes=("エントリー制なしのため、未選句者の自動判定は行いません。",),
        )
    if not participants:
        return ProgressReport(
            kind="selecting",
            complete=False,
            incomplete=(),
            notes=("承認済み参加者がいません。",),
        )

    labels_result = await session.execute(
        select(SelectLabel)
        .where(SelectLabel.kukai_id == kukai.id)
        .order_by(SelectLabel.display_order)
    )
    labels = [
        label
        for label in labels_result.scalars().all()
        if label.label != select_rule_service.AUTHOR_COMMENT_LABEL
    ]
    if not labels:
        return ProgressReport(
            kind="selecting",
            complete=True,
            incomplete=(),
            notes=("作者コメント以外の選句ラベルがありません。",),
        )

    all_selects = await select_repo.get_all_selects(session, kukai.id)
    selects_by_user: dict[int, list] = defaultdict(list)
    for row in all_selects:
        if not row.is_self_comment:
            selects_by_user[row.selector_user_id].append(row)

    incomplete: list[IncompleteParticipant] = []
    for entry in participants:
        if entry.is_special:
            continue
        user_selects = selects_by_user.get(entry.user_id, [])
        label_counts = Counter(row.select_label_id for row in user_selects)
        issues: list[str] = []
        for label in labels:
            count = label_counts.get(label.id, 0)
            if count < label.min_count:
                issues.append(f"{label.label} {count}/{label.min_count}")
            if label.comment_mode == "required":
                missing_comments = sum(
                    1
                    for row in user_selects
                    if row.select_label_id == label.id and row.comment is None
                )
                if missing_comments:
                    issues.append(f"{label.label} コメント未入力 {missing_comments}件")
        if issues:
            incomplete.append(
                IncompleteParticipant(
                    user_id=entry.user_id,
                    display_name=_entry_display_name(entry),
                    issues=tuple(issues),
                )
            )

    return ProgressReport(kind="selecting", complete=not incomplete, incomplete=tuple(incomplete))


async def report_for_state(session: AsyncSession, kukai, state) -> ProgressReport | None:
    from bot.state_machine.states import KukaiState

    if state == KukaiState.SUBMISSION_OPEN:
        return await submission_report(session, kukai)
    if state == KukaiState.SELECTING_OPEN:
        return await selecting_report(session, kukai)
    return None


async def _required_participants(session: AsyncSession, kukai) -> list[Entry] | None:
    if not kukai.entry_enabled:
        return None
    result = await session.execute(
        select(Entry)
        .where(Entry.kukai_id == kukai.id, Entry.status == "approved")
        .order_by(Entry.created_at)
    )
    return list(result.scalars().all())


def _entry_display_name(entry: Entry) -> str:
    return entry.haigo or f"UID:{entry.user_id}"
