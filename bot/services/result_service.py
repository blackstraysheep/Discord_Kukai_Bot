"""Result aggregation and ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.submission import PublishedSubmission
from bot.models.select import Select
from bot.models.select_rule import SelectLabel
from bot.repositories import select_repo
from bot.services.errors import InvalidStateError
from bot.state_machine.states import KukaiState

if TYPE_CHECKING:
    pass

_RESULT_ALLOWED = {
    KukaiState.SELECTING_CLOSED,
    KukaiState.RESULTS,
    KukaiState.ENDED,
}


@dataclass
class LabelSelects:
    label: str
    point: int
    rank_priority: int
    count: int = 0
    comments: list[str] = field(default_factory=list)


@dataclass
class SubmissionResult:
    number: int
    text: str
    author_user_id: int
    total_score: int
    label_selects: list[LabelSelects]  # ordered by rank_priority asc
    rank: int = 0  # populated by compute_results


async def compute_results(
    session: AsyncSession,
    kukai,
) -> list[SubmissionResult]:
    """Aggregate selects and rank all published submissions."""
    if KukaiState.from_value(kukai.state) not in _RESULT_ALLOWED:
        raise InvalidStateError("結果はまだ集計できません。")

    # Load published submissions with their submissions
    ps_result = await session.execute(
        select(PublishedSubmission)
        .where(PublishedSubmission.kukai_id == kukai.id)
        .order_by(PublishedSubmission.number)
        .options(selectinload(PublishedSubmission.submission))
    )
    pub_subs = list(ps_result.scalars().all())

    # Load all selects for this kukai
    all_selects = await select_repo.get_all_selects(session, kukai.id)

    # Load select labels ordered by rank_priority
    labels_result = await session.execute(
        select(SelectLabel)
        .where(SelectLabel.kukai_id == kukai.id)
        .order_by(SelectLabel.rank_priority)
    )
    labels = list(labels_result.scalars().all())
    label_map = {lbl.id: lbl for lbl in labels}

    # Group selects by submission_id
    selects_by_sub: dict[int, list[Select]] = {}
    for selected in all_selects:
        selects_by_sub.setdefault(selected.submission_id, []).append(selected)

    # Build results
    results: list[SubmissionResult] = []
    for ps in pub_subs:
        sub_selects = selects_by_sub.get(ps.submission_id, [])

        # Aggregate by label
        lv_map: dict[int, LabelSelects] = {
            lbl.id: LabelSelects(lbl.label, lbl.point, lbl.rank_priority)
            for lbl in labels
        }
        total_score = 0
        for selected in sub_selects:
            lbl = label_map.get(selected.select_label_id)
            if not lbl:
                continue
            lv_map[selected.select_label_id].count += 1
            total_score += lbl.point
            if selected.comment:
                lv_map[selected.select_label_id].comments.append(selected.comment.comment)

        label_selects = sorted(
            [lv for lv in lv_map.values() if lv.count > 0],
            key=lambda lv: lv.rank_priority,
        )

        results.append(
            SubmissionResult(
                number=ps.number,
                text=ps.submission.text,
                author_user_id=ps.submission.user_id,
                total_score=total_score,
                label_selects=label_selects,
            )
        )

    # Sort: score desc, then tie-break by label rank_priority
    def _sort_key(r: SubmissionResult) -> tuple:
        # For tie-breaking: count of selects at each rank priority level
        # Lower rank_priority = higher priority label
        prio_counts = {
            lv.rank_priority: lv.count
            for lv in r.label_selects
            if lv.label != "作者コメント"
        }
        # Create a tuple of counts at each priority level (lower index = higher priority)
        max_prio = max((lbl.rank_priority for lbl in labels), default=1)
        tie_breaker = tuple(-(prio_counts.get(p, 0)) for p in range(1, max_prio + 1))
        return (-r.total_score,) + tie_breaker

    results.sort(key=_sort_key)

    # Assign ranks (ties share rank)
    rank = 1
    for i, r in enumerate(results):
        if i > 0 and _sort_key(r) != _sort_key(results[i - 1]):
            rank = i + 1
        r.rank = rank

    return results
