"""Unit tests for select_service."""

from datetime import timedelta, timezone, datetime

import pytest

from bot.services import entry_service, kukai_service, submission_service, select_service
from bot.services.errors import InvalidStateError, NotFoundError, ValidationError
from bot.state_machine.states import KukaiState


def _utc(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


async def _setup_selecting(session, *, entry_enabled=False, max_count=None, comment_mode="none"):
    """Create a kukai and advance to SELECTING_OPEN with one published submission."""
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
    await session.flush()

    # Customize first label (特選) with max_count / comment_mode
    if kukai.select_labels:
        lbl = kukai.select_labels[0]
        lbl.max_count = max_count
        lbl.comment_mode = comment_mode
        await session.flush()

    # Advance to submission_open
    while KukaiState(kukai.state) != KukaiState.SUBMISSION_OPEN:
        await kukai_service.proceed(session, kukai)

    # Submit haiku by user_id=1 (not selector)
    await submission_service.submit(session, kukai, user_id=1, text="春の海")

    # Advance to waiting_publish
    await kukai_service.proceed(session, kukai)  # → submission_closed
    await kukai_service.proceed(session, kukai)  # → waiting_publish

    # Publish and advance to selecting_open
    await submission_service.publish(session, kukai)
    await kukai_service.proceed(session, kukai)  # → selecting_open

    await session.commit()
    return kukai


async def _get_submission_id(session, kukai_id):
    from bot.repositories import submission_repo
    pub = await submission_repo.list_published(session, kukai_id)
    return pub[0].submission_id


@pytest.mark.asyncio
async def test_cast_select_basic(db_session):
    kukai = await _setup_selecting(db_session)
    sub_id = await _get_submission_id(db_session, kukai.id)
    label_id = kukai.select_labels[0].id

    sel = await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub_id, select_label_id=label_id)
    assert sel.selector_user_id == 2
    assert sel.select_label_id == label_id


@pytest.mark.asyncio
async def test_cast_select_own_haiku_raises(db_session):
    kukai = await _setup_selecting(db_session)
    sub_id = await _get_submission_id(db_session, kukai.id)
    label_id = kukai.select_labels[0].id

    # user_id=1 submitted the haiku
    with pytest.raises(ValidationError):
        await select_service.cast_select(db_session, kukai, selector_user_id=1, submission_id=sub_id, select_label_id=label_id)


@pytest.mark.asyncio
async def test_cast_select_wrong_state_raises(db_session):
    kukai = await _setup_selecting(db_session)
    sub_id = await _get_submission_id(db_session, kukai.id)
    label_id = kukai.select_labels[0].id

    # Advance to selecting_closed
    await kukai_service.proceed(db_session, kukai)
    await db_session.commit()

    with pytest.raises(InvalidStateError):
        await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub_id, select_label_id=label_id)


@pytest.mark.asyncio
async def test_cast_select_max_count_exceeded(db_session):
    kukai = await _setup_selecting(db_session, max_count=1)
    sub_id = await _get_submission_id(db_session, kukai.id)
    label_id = kukai.select_labels[0].id

    # First select on sub_id
    await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub_id, select_label_id=label_id)

    # Try to select same label on a non-existent second submission
    # Since we only have one published submission, we need to test via a fresh kukai with 2 subs
    # Simpler: just verify count is 1 and re-selecting same sub doesn't fail (same label = no additional usage)
    sel = await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub_id, select_label_id=label_id)
    assert sel is not None  # Re-selecting same label on same sub is OK


@pytest.mark.asyncio
async def test_cast_select_max_count_two_subs(db_session):
    """Verify max_count=1 blocks a second submission being selected with the same label."""
    kukai = await kukai_service.create_kukai(
        db_session, guild_id=1, created_by=100, channel_id=200, title="X",
        submission_close_at=_utc(7), selecting_close_at=_utc(14), entry_enabled=False,
    )
    kukai.select_labels[0].max_count = 1
    await db_session.flush()

    while KukaiState(kukai.state) != KukaiState.SUBMISSION_OPEN:
        await kukai_service.proceed(db_session, kukai)

    await submission_service.submit(db_session, kukai, user_id=1, text="春の海")
    await submission_service.submit(db_session, kukai, user_id=3, text="夏の川")

    await kukai_service.proceed(db_session, kukai)  # → submission_closed
    await kukai_service.proceed(db_session, kukai)  # → waiting_publish
    await submission_service.publish(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)  # → selecting_open
    await db_session.commit()

    from bot.repositories import submission_repo
    pub = await submission_repo.list_published(db_session, kukai.id)
    sub1_id = pub[0].submission_id
    sub2_id = pub[1].submission_id
    label_id = kukai.select_labels[0].id

    # First select OK
    await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub1_id, select_label_id=label_id)

    # Second select on different sub with same label → should fail (max=1 reached)
    with pytest.raises(ValidationError):
        await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub2_id, select_label_id=label_id)


@pytest.mark.asyncio
async def test_cast_select_updates_label(db_session):
    kukai = await _setup_selecting(db_session)
    sub_id = await _get_submission_id(db_session, kukai.id)
    label1_id = kukai.select_labels[0].id
    label2_id = kukai.select_labels[1].id

    await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub_id, select_label_id=label1_id)
    sel = await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub_id, select_label_id=label2_id)
    assert sel.select_label_id == label2_id


@pytest.mark.asyncio
async def test_cast_select_with_comment(db_session):
    kukai = await _setup_selecting(db_session, comment_mode="optional")
    sub_id = await _get_submission_id(db_session, kukai.id)
    label_id = kukai.select_labels[0].id

    sel = await select_service.cast_select(
        db_session, kukai, selector_user_id=2, submission_id=sub_id,
        select_label_id=label_id, comment="素晴らしい句です"
    )
    assert sel.comment is not None
    assert sel.comment.comment == "素晴らしい句です"


@pytest.mark.asyncio
async def test_cast_select_author_comment_saved(db_session):
    kukai = await _setup_selecting(db_session)
    sub_id = await _get_submission_id(db_session, kukai.id)
    author_label_id = next(lbl.id for lbl in kukai.select_labels if lbl.label == "作者コメント")

    sel = await select_service.cast_select(
        db_session,
        kukai,
        selector_user_id=1,
        submission_id=sub_id,
        select_label_id=author_label_id,
        comment="この句は春の朝を詠みました",
        is_self_comment=True,
    )
    await db_session.commit()

    assert sel.is_self_comment is True
    assert sel.comment is not None
    assert sel.comment.comment == "この句は春の朝を詠みました"

    from bot.repositories import select_repo

    loaded = await select_repo.get_select(db_session, kukai.id, 1, sub_id)
    assert loaded is not None
    assert loaded.is_self_comment is True
    assert loaded.comment is not None
    assert loaded.comment.comment == "この句は春の朝を詠みました"


@pytest.mark.asyncio
async def test_cast_select_required_comment_missing_raises(db_session):
    kukai = await _setup_selecting(db_session, comment_mode="required")
    sub_id = await _get_submission_id(db_session, kukai.id)
    label_id = kukai.select_labels[0].id

    with pytest.raises(ValidationError):
        await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub_id, select_label_id=label_id)


@pytest.mark.asyncio
async def test_remove_select(db_session):
    kukai = await _setup_selecting(db_session)
    sub_id = await _get_submission_id(db_session, kukai.id)
    label_id = kukai.select_labels[0].id

    await select_service.cast_select(db_session, kukai, selector_user_id=2, submission_id=sub_id, select_label_id=label_id)
    await select_service.remove_select(db_session, kukai, selector_user_id=2, submission_id=sub_id)

    from bot.repositories import select_repo
    sel = await select_repo.get_select(db_session, kukai.id, 2, sub_id)
    assert sel is None


@pytest.mark.asyncio
async def test_remove_select_not_found_raises(db_session):
    kukai = await _setup_selecting(db_session)
    sub_id = await _get_submission_id(db_session, kukai.id)

    with pytest.raises(NotFoundError):
        await select_service.remove_select(db_session, kukai, selector_user_id=2, submission_id=sub_id)


@pytest.mark.asyncio
async def test_overall_comment_upsert(db_session):
    kukai = await _setup_selecting(db_session)

    oc = await select_service.set_overall_comment(db_session, kukai, user_id=2, text="良い句会でした")
    assert oc.comment == "良い句会でした"

    oc2 = await select_service.set_overall_comment(db_session, kukai, user_id=2, text="更新しました")
    assert oc2.comment == "更新しました"
