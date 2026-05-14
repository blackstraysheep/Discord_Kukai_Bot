"""Result aggregation and ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.submission import PublishedSubmission
from bot.models.vote import Vote
from bot.models.vote_rule import VoteLabel
from bot.repositories import vote_repo
from bot.services.errors import InvalidStateError
from bot.state_machine.states import KukaiState

if TYPE_CHECKING:
    pass

_RESULT_ALLOWED = {
    KukaiState.VOTING_CLOSED,
    KukaiState.WAITING_RESULTS,
    KukaiState.RESULTS,
    KukaiState.ENDED,
}


@dataclass
class LabelVotes:
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
    label_votes: list[LabelVotes]  # ordered by rank_priority asc
    rank: int = 0  # populated by compute_results


async def compute_results(
    session: AsyncSession,
    kukai,
) -> list[SubmissionResult]:
    """Aggregate votes and rank all published submissions."""
    if KukaiState(kukai.state) not in _RESULT_ALLOWED:
        raise InvalidStateError("結果はまだ集計できません。")

    # Load published submissions with their submissions
    ps_result = await session.execute(
        select(PublishedSubmission)
        .where(PublishedSubmission.kukai_id == kukai.id)
        .order_by(PublishedSubmission.number)
        .options(selectinload(PublishedSubmission.submission))
    )
    pub_subs = list(ps_result.scalars().all())

    # Load all votes for this kukai
    all_votes = await vote_repo.get_all_votes(session, kukai.id)

    # Load vote labels ordered by rank_priority
    labels_result = await session.execute(
        select(VoteLabel)
        .where(VoteLabel.kukai_id == kukai.id)
        .order_by(VoteLabel.rank_priority)
    )
    labels = list(labels_result.scalars().all())
    label_map = {lbl.id: lbl for lbl in labels}

    # Group votes by submission_id
    votes_by_sub: dict[int, list[Vote]] = {}
    for vote in all_votes:
        votes_by_sub.setdefault(vote.submission_id, []).append(vote)

    # Build results
    results: list[SubmissionResult] = []
    for ps in pub_subs:
        sub_votes = votes_by_sub.get(ps.submission_id, [])

        # Aggregate by label
        lv_map: dict[int, LabelVotes] = {
            lbl.id: LabelVotes(lbl.label, lbl.point, lbl.rank_priority)
            for lbl in labels
        }
        total_score = 0
        for vote in sub_votes:
            lbl = label_map.get(vote.vote_label_id)
            if not lbl:
                continue
            lv_map[vote.vote_label_id].count += 1
            total_score += lbl.point
            if vote.comment:
                lv_map[vote.vote_label_id].comments.append(vote.comment.comment)

        label_votes = sorted(
            [lv for lv in lv_map.values() if lv.count > 0],
            key=lambda lv: lv.rank_priority,
        )

        results.append(
            SubmissionResult(
                number=ps.number,
                text=ps.submission.text,
                author_user_id=ps.submission.user_id,
                total_score=total_score,
                label_votes=label_votes,
            )
        )

    # Sort: score desc, then tie-break by label rank_priority
    def _sort_key(r: SubmissionResult) -> tuple:
        # For tie-breaking: count of votes at each rank priority level
        # Lower rank_priority = higher priority label
        prio_counts = {lv.rank_priority: lv.count for lv in r.label_votes}
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
