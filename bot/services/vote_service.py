"""Voting (選句) lifecycle operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.submission import PublishedSubmission
from bot.models.vote import OverallComment, Vote, VoteComment
from bot.models.vote_rule import VoteLabel
from bot.repositories import vote_repo
from bot.services.errors import InvalidStateError, NotFoundError, ValidationError
from bot.state_machine.states import KukaiState
from bot.utils.text import normalize

_VOTING_OPEN = KukaiState.VOTING_OPEN


async def cast_vote(
    session: AsyncSession,
    kukai,
    voter_user_id: int,
    submission_id: int,
    vote_label_id: int,
    comment: str | None = None,
) -> Vote:
    """Cast or update a vote on a published submission."""
    if KukaiState(kukai.state) != _VOTING_OPEN:
        raise InvalidStateError("現在選句を受け付けていません。")

    # Verify published submission exists and belongs to this kukai
    ps_result = await session.execute(
        select(PublishedSubmission)
        .where(PublishedSubmission.submission_id == submission_id)
        .options(selectinload(PublishedSubmission.submission))
    )
    ps = ps_result.scalar_one_or_none()
    if not ps or ps.kukai_id != kukai.id:
        raise NotFoundError("投句が見つかりません。")

    if ps.submission.user_id == voter_user_id:
        raise ValidationError("自分の句には選句できません。")

    # Verify label belongs to this kukai
    label = await session.get(VoteLabel, vote_label_id)
    if not label or label.kukai_id != kukai.id:
        raise NotFoundError("選句ラベルが見つかりません。")

    # Max-count check: count existing usage EXCLUDING current submission
    existing = await vote_repo.get_vote(session, kukai.id, voter_user_id, submission_id)
    already_uses_new_label = existing is not None and existing.vote_label_id == vote_label_id
    if not already_uses_new_label and label.max_count is not None:
        current_usage = await vote_repo.count_label_usage(
            session, kukai.id, voter_user_id, vote_label_id
        )
        if current_usage >= label.max_count:
            raise ValidationError(
                f"「{label.label}」の選句数が上限（{label.max_count}）に達しています。"
            )

    # Comment validation
    comment_text = normalize(comment.strip()) if comment and comment.strip() else None
    if label.comment_mode == "required" and not comment_text:
        raise ValidationError("このラベルにはコメントが必須です。")

    # Upsert vote
    if existing:
        existing.vote_label_id = vote_label_id
        vote = existing
        # existing was loaded with selectinload(comment), so vote.comment is safe
        if comment_text:
            if vote.comment:
                vote.comment.comment = comment_text
            else:
                vc = VoteComment(vote_id=vote.id, comment=comment_text)
                session.add(vc)
                vote.comment = vc
        await session.flush()
    else:
        vote = Vote(
            kukai_id=kukai.id,
            voter_user_id=voter_user_id,
            submission_id=submission_id,
            vote_label_id=vote_label_id,
        )
        session.add(vote)
        await session.flush()
        if comment_text:
            vc = VoteComment(vote_id=vote.id, comment=comment_text)
            session.add(vc)
            vote.comment = vc  # attach to avoid lazy-load
            await session.flush()

    return vote


async def remove_vote(
    session: AsyncSession,
    kukai,
    voter_user_id: int,
    submission_id: int,
) -> None:
    """Remove a vote (only during voting_open)."""
    if KukaiState(kukai.state) != _VOTING_OPEN:
        raise InvalidStateError("選句の取消は受付期間中のみ可能です。")

    vote = await vote_repo.get_vote(session, kukai.id, voter_user_id, submission_id)
    if not vote:
        raise NotFoundError("選句が見つかりません。")

    await session.delete(vote)


async def set_overall_comment(
    session: AsyncSession,
    kukai,
    user_id: int,
    text: str,
) -> OverallComment:
    """Upsert overall comment (総評)."""
    if KukaiState(kukai.state) != _VOTING_OPEN:
        raise InvalidStateError("総評の入力は選句期間中のみ可能です。")

    text = normalize(text.strip())
    if not text:
        raise ValidationError("総評の本文が空です。")

    existing = await vote_repo.get_overall_comment(session, kukai.id, user_id)
    if existing:
        existing.comment = text
        return existing

    oc = OverallComment(kukai_id=kukai.id, user_id=user_id, comment=text)
    session.add(oc)
    await session.flush()
    return oc


async def list_votes_for_voter(
    session: AsyncSession, kukai_id: int, voter_user_id: int
) -> list[Vote]:
    return await vote_repo.get_votes_by_voter(session, kukai_id, voter_user_id)
