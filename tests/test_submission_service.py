"""Unit tests for submission_service."""

from datetime import timedelta, timezone, datetime

import pytest
from sqlalchemy import func, select

from bot.models.select import Select
from bot.models.select_rule import SelectLabel
from bot.models.submission import PublishedSubmission, Submission
from bot.services import kukai_service, select_service, submission_service, entry_service
from bot.services.errors import InvalidStateError, NotFoundError, ValidationError
from bot.state_machine.states import KukaiState


def _utc(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


async def _make_kukai(
    session,
    *,
    entry_enabled=True,
    submission_min=1,
    submission_max=3,
    submission_overflow=False,
    submission_incomplete="keep",
):
    kukai = await kukai_service.create_kukai(
        session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="テスト句会",
        entry_close_at=_utc(3) if entry_enabled else None,
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=entry_enabled,
    )
    kukai.submission_min = submission_min
    kukai.submission_max = submission_max
    kukai.submission_overflow = submission_overflow
    kukai.submission_incomplete = submission_incomplete
    await session.flush()
    return kukai


async def _advance_to_submission_open(session, kukai):
    """Advance kukai to SUBMISSION_OPEN."""
    while KukaiState(kukai.state) != KukaiState.SUBMISSION_OPEN:
        await kukai_service.proceed(session, kukai)
    await session.commit()


@pytest.mark.asyncio
async def test_submit_basic(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    await _advance_to_submission_open(db_session, kukai)

    sub, over = await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    assert sub.text == "春の海"
    assert sub.kukai_id == kukai.id
    assert not over


@pytest.mark.asyncio
async def test_submit_normalizes_text(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    await _advance_to_submission_open(db_session, kukai)

    # NFC normalization: combining character sequence → precomposed
    sub, _ = await submission_service.submit(db_session, kukai, user_id=1, text="  春の海  ")
    assert sub.text == "春の海"


@pytest.mark.asyncio
async def test_submit_wrong_state_raises(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    kukai.state = KukaiState.DRAFT
    with pytest.raises(InvalidStateError):
        await submission_service.submit(db_session, kukai, user_id=1, text="春の海")


@pytest.mark.asyncio
async def test_submit_requires_entry_approval(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=True)
    await _advance_to_submission_open(db_session, kukai)

    # User not entered → error
    with pytest.raises(InvalidStateError):
        await submission_service.submit(db_session, kukai, user_id=1, text="春の海")


@pytest.mark.asyncio
async def test_submit_with_approved_entry(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=True)
    await entry_service.enter(db_session, kukai, user_id=1)
    await kukai_service.proceed(db_session, kukai)
    await db_session.commit()

    sub, _ = await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    assert sub.text == "春の海"


@pytest.mark.asyncio
async def test_submit_max_exceeded_raises(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False, submission_max=2)
    await _advance_to_submission_open(db_session, kukai)

    await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    await submission_service.submit(db_session, kukai, user_id=1, text="夏の川")
    with pytest.raises(ValidationError):
        await submission_service.submit(db_session, kukai, user_id=1, text="秋の山")


@pytest.mark.asyncio
async def test_submit_overflow_allowed(db_session):
    kukai = await _make_kukai(
        db_session, entry_enabled=False, submission_max=1, submission_overflow=True
    )
    await _advance_to_submission_open(db_session, kukai)

    await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    sub, over = await submission_service.submit(db_session, kukai, user_id=1, text="夏の川")
    assert over is True


@pytest.mark.asyncio
async def test_submit_unlimited_when_submission_max_none(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False, submission_max=None)
    await _advance_to_submission_open(db_session, kukai)

    for idx in range(10):
        sub, over = await submission_service.submit(
            db_session, kukai, user_id=1, text=f"句{idx}"
        )
        assert sub is not None
        assert over is False


@pytest.mark.asyncio
async def test_submit_unlimited_when_created_with_submission_max_none(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="無制限句会",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=False,
        submission_max=None,
    )
    await _advance_to_submission_open(db_session, kukai)

    for idx in range(6):
        sub, over = await submission_service.submit(
            db_session, kukai, user_id=1, text=f"句{idx}"
        )
        assert sub is not None
        assert over is False


@pytest.mark.asyncio
async def test_edit(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    await _advance_to_submission_open(db_session, kukai)

    sub, _ = await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    edited = await submission_service.edit(
        db_session, kukai, user_id=1, submission_id=sub.id, new_text="秋の山"
    )
    assert edited.text == "秋の山"


@pytest.mark.asyncio
async def test_edit_wrong_owner_raises(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    await _advance_to_submission_open(db_session, kukai)

    sub, _ = await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    with pytest.raises(NotFoundError):
        await submission_service.edit(
            db_session, kukai, user_id=2, submission_id=sub.id, new_text="夏の川"
        )


@pytest.mark.asyncio
async def test_delete(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    await _advance_to_submission_open(db_session, kukai)

    sub, _ = await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    await submission_service.delete_submission(db_session, kukai, user_id=1, submission_id=sub.id)

    subs = await submission_service.list_user_submissions(db_session, kukai.id, user_id=1)
    assert subs == []


@pytest.mark.asyncio
async def test_delete_wrong_state_raises(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    await _advance_to_submission_open(db_session, kukai)

    sub, _ = await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    await kukai_service.proceed(db_session, kukai)  # → submission_closed
    await db_session.commit()

    with pytest.raises(InvalidStateError):
        await submission_service.delete_submission(
            db_session, kukai, user_id=1, submission_id=sub.id
        )


@pytest.mark.asyncio
async def test_publish_assigns_random_numbers(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    await _advance_to_submission_open(db_session, kukai)

    await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    await submission_service.submit(db_session, kukai, user_id=2, text="夏の川")
    await submission_service.submit(db_session, kukai, user_id=3, text="秋の山")

    # Advance to waiting_publish
    await kukai_service.proceed(db_session, kukai)  # → submission_closed
    await kukai_service.proceed(db_session, kukai)  # → waiting_publish
    await db_session.commit()

    published = await submission_service.publish(db_session, kukai)
    assert len(published) == 3
    numbers = [ps.number for ps in published]
    assert sorted(numbers) == [1, 2, 3]


@pytest.mark.asyncio
async def test_publish_wrong_state_raises(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    await _advance_to_submission_open(db_session, kukai)

    with pytest.raises(InvalidStateError):
        await submission_service.publish(db_session, kukai)


@pytest.mark.asyncio
async def test_publish_discard_incomplete(db_session):
    kukai = await _make_kukai(
        db_session,
        entry_enabled=False,
        submission_min=2,
        submission_max=3,
        submission_incomplete="discard",
    )
    await _advance_to_submission_open(db_session, kukai)

    # User 1: 2 submissions (complete)
    await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    await submission_service.submit(db_session, kukai, user_id=1, text="春の川")
    # User 2: 1 submission (incomplete → discard)
    await submission_service.submit(db_session, kukai, user_id=2, text="夏の海")

    await kukai_service.proceed(db_session, kukai)  # → submission_closed
    await kukai_service.proceed(db_session, kukai)  # → waiting_publish
    await db_session.commit()

    published = await submission_service.publish(db_session, kukai)
    assert len(published) == 2
    texts = {ps.submission.text for ps in published}
    assert "夏の海" not in texts


@pytest.mark.asyncio
async def test_rollback_publish(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    await _advance_to_submission_open(db_session, kukai)

    await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    await kukai_service.proceed(db_session, kukai)  # → submission_closed
    await kukai_service.proceed(db_session, kukai)  # → waiting_publish
    await db_session.commit()

    await submission_service.publish(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)  # → selecting_open
    await db_session.commit()

    await submission_service.rollback_publish(db_session, kukai, reset_selects=False)
    await db_session.commit()

    from bot.repositories import submission_repo
    published = await submission_repo.list_published(db_session, kukai.id)
    assert published == []


async def _make_selecting_kukai(session):
    kukai = await _make_kukai(session, entry_enabled=False)
    await _advance_to_submission_open(session, kukai)
    await submission_service.submit(session, kukai, user_id=1, text="春の海")
    await submission_service.submit(session, kukai, user_id=2, text="夏の川")
    await kukai_service.proceed(session, kukai)  # → submission_closed
    await kukai_service.proceed(session, kukai)  # → waiting_publish
    await session.commit()
    published = await submission_service.publish(session, kukai)
    await kukai_service.proceed(session, kukai)  # → selecting_open
    label = await _first_select_label(session, kukai.id)
    await select_service.cast_select(
        session,
        kukai,
        selector_user_id=3,
        submission_id=published[0].submission_id,
        select_label_id=label.id,
    )
    await session.commit()
    return kukai


async def _first_select_label(session, kukai_id: int) -> SelectLabel:
    result = await session.execute(
        select(SelectLabel)
        .where(SelectLabel.kukai_id == kukai_id, SelectLabel.label != "作者コメント")
        .order_by(SelectLabel.display_order)
    )
    return result.scalars().first()


async def _count(session, model, kukai_id: int) -> int:
    result = await session.execute(select(func.count()).where(model.kukai_id == kukai_id))
    return result.scalar_one()


@pytest.mark.asyncio
async def test_rollback_to_submission_open_keeps_submissions_and_selects(db_session):
    kukai = await _make_selecting_kukai(db_session)

    await submission_service.rollback_to_state(
        db_session,
        kukai,
        KukaiState.SUBMISSION_OPEN,
        keep_submissions=True,
        keep_selects=True,
    )
    await db_session.commit()

    assert kukai.state == KukaiState.SUBMISSION_OPEN
    assert await _count(db_session, Submission, kukai.id) == 2
    assert await _count(db_session, Select, kukai.id) == 1
    assert await _count(db_session, PublishedSubmission, kukai.id) == 0


@pytest.mark.asyncio
async def test_rollback_to_submission_open_resets_submissions_and_selects(db_session):
    kukai = await _make_selecting_kukai(db_session)

    await submission_service.rollback_to_state(
        db_session,
        kukai,
        KukaiState.SUBMISSION_OPEN,
        keep_submissions=False,
        keep_selects=False,
    )
    await db_session.commit()

    assert kukai.state == KukaiState.SUBMISSION_OPEN
    assert await _count(db_session, Submission, kukai.id) == 0
    assert await _count(db_session, Select, kukai.id) == 0
    assert await _count(db_session, PublishedSubmission, kukai.id) == 0


@pytest.mark.asyncio
async def test_rollback_to_selecting_open_keeps_published_submissions(db_session):
    kukai = await _make_selecting_kukai(db_session)
    await kukai_service.proceed(db_session, kukai)  # → selecting_closed
    await kukai_service.proceed(db_session, kukai)  # → results
    await db_session.commit()

    await submission_service.rollback_to_state(
        db_session,
        kukai,
        KukaiState.SELECTING_OPEN,
        keep_submissions=True,
        keep_selects=True,
    )
    await db_session.commit()

    assert kukai.state == KukaiState.SELECTING_OPEN
    assert await _count(db_session, Submission, kukai.id) == 2
    assert await _count(db_session, Select, kukai.id) == 1
    assert await _count(db_session, PublishedSubmission, kukai.id) == 2
