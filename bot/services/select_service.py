"""Selecting (選句) lifecycle operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.submission import PublishedSubmission
from bot.models.select import OverallSelectComment, Select, SelectComment
from bot.models.select_rule import SelectLabel
from bot.repositories import select_repo
from bot.services.errors import InvalidStateError, NotFoundError, ValidationError
from bot.state_machine.states import KukaiState
from bot.utils.text import normalize

_SELECTING_OPEN = KukaiState.SELECTING_OPEN


async def cast_select(
    session: AsyncSession,
    kukai,
    selector_user_id: int,
    submission_id: int,
    select_label_id: int,
    comment: str | None = None,
    *,
    is_self_comment: bool = False,
) -> Select:
    """Cast or update a selection on a published submission."""
    if KukaiState.from_value(kukai.state) != _SELECTING_OPEN:
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

    is_own_submission = ps.submission.user_id == selector_user_id
    if is_own_submission and not is_self_comment:
        raise ValidationError("自分の句には通常の選句はできません（作者コメントのみ可）。")
    if not is_own_submission and is_self_comment:
        raise ValidationError("作者コメントは自分の句に対してのみ設定できます。")

    # Verify label belongs to this kukai
    label = await session.get(SelectLabel, select_label_id)
    if not label or label.kukai_id != kukai.id:
        raise NotFoundError("選句ラベルが見つかりません。")
    if is_self_comment and label.label != "作者コメント":
        raise ValidationError("作者コメントには「作者コメント」ラベルを使用してください。")
    if not is_self_comment and label.label == "作者コメント":
        raise ValidationError("「作者コメント」ラベルは自分の句にのみ使用できます。")

    # Max-count check: count existing usage EXCLUDING current submission
    existing = await select_repo.get_select(session, kukai.id, selector_user_id, submission_id)
    already_uses_new_label = existing is not None and existing.select_label_id == select_label_id
    if not is_self_comment and not already_uses_new_label and label.max_count is not None:
        current_usage = await select_repo.count_label_usage(
            session, kukai.id, selector_user_id, select_label_id
        )
        if current_usage >= label.max_count:
            raise ValidationError(
                f"「{label.label}」の選句数が上限（{label.max_count}）に達しています。"
            )

    # Comment validation
    comment_text = normalize(comment.strip()) if comment and comment.strip() else None
    if (label.comment_mode == "required" or is_self_comment) and not comment_text:
        raise ValidationError("このラベルにはコメントが必須です。")

    # Upsert selection
    if existing:
        existing.select_label_id = select_label_id
        existing.is_self_comment = is_self_comment
        sel = existing
        # existing was loaded with selectinload(comment), so sel.comment is safe
        if comment_text:
            if sel.comment:
                sel.comment.comment = comment_text
            else:
                vc = SelectComment(select_id=sel.id, comment=comment_text)
                session.add(vc)
                sel.comment = vc
        await session.flush()
    else:
        sel = Select(
            kukai_id=kukai.id,
            selector_user_id=selector_user_id,
            submission_id=submission_id,
            select_label_id=select_label_id,
            is_self_comment=is_self_comment,
        )
        session.add(sel)
        await session.flush()
        if comment_text:
            vc = SelectComment(select_id=sel.id, comment=comment_text)
            session.add(vc)
            sel.comment = vc  # attach to avoid lazy-load
            await session.flush()

    return sel


async def remove_select(
    session: AsyncSession,
    kukai,
    selector_user_id: int,
    submission_id: int,
) -> None:
    """Remove a selection (only during SELECTING_OPEN)."""
    if KukaiState.from_value(kukai.state) != _SELECTING_OPEN:
        raise InvalidStateError("選句の取消は受付期間中のみ可能です。")

    sel = await select_repo.get_select(session, kukai.id, selector_user_id, submission_id)
    if not sel:
        raise NotFoundError("選句が見つかりません。")

    await session.delete(sel)


async def set_overall_comment(
    session: AsyncSession,
    kukai,
    user_id: int,
    text: str,
) -> OverallSelectComment:
    """Upsert overall comment (総評)."""
    if KukaiState.from_value(kukai.state) != _SELECTING_OPEN:
        raise InvalidStateError("総評の入力は選句期間中のみ可能です。")

    text = normalize(text.strip())
    if not text:
        raise ValidationError("総評の本文が空です。")

    existing = await select_repo.get_overall_comment(session, kukai.id, user_id)
    if existing:
        existing.comment = text
        return existing

    oc = OverallSelectComment(kukai_id=kukai.id, user_id=user_id, comment=text)
    session.add(oc)
    await session.flush()
    return oc


async def list_selects_for_selector(
    session: AsyncSession, kukai_id: int, selector_user_id: int
) -> list[Select]:
    return await select_repo.get_selects_by_selector(session, kukai_id, selector_user_id)
