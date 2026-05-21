"""Unit tests for Phase 9 services."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.models.entry import Entry
from bot.models.kukai import KukaiAdmin
from bot.models.submission import PublishedSubmission, Submission
from bot.models.select import Select
from bot.repositories import kukai_repo
from bot.services import entry_service, export_service, kukai_service, submission_service, select_service
from bot.services.errors import InvalidStateError
from bot.state_machine.states import KukaiState


def _utc(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


async def _make_kukai(session, *, entry_enabled: bool = True, entry_approval: bool = False):
    return await kukai_service.create_kukai(
        session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="Phase9テスト句会",
        entry_close_at=_utc(3) if entry_enabled else None,
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=entry_enabled,
        entry_approval=entry_approval,
    )


@pytest.mark.asyncio
async def test_edit_kukai_updates_fields_and_deadlines(db_session):
    kukai = await _make_kukai(db_session)
    new_submission_close = _utc(10)
    new_selecting_close = _utc(15)

    changed = await kukai_service.edit_kukai(
        db_session,
        kukai,
        title="更新後タイトル",
        submission_close_at=new_submission_close,
        selecting_close_at=new_selecting_close,
        submission_min=2,
        submission_max=4,
        submission_mode="semi_auto",
        publish_mode="auto",
        result_mode="manual",
        author_reveal=False,
    )

    assert changed is True
    assert kukai.title == "更新後タイトル"
    assert kukai.submission_close_at == new_submission_close
    assert kukai.selecting_close_at == new_selecting_close
    assert kukai.submission_min == 2
    assert kukai.submission_max == 4
    assert kukai.submission_mode == "semi_auto"
    assert kukai.publish_mode == "auto"
    assert kukai.result_mode == "manual"
    assert kukai.author_reveal is False


@pytest.mark.asyncio
async def test_edit_kukai_blocks_submission_settings_after_selecting_open(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=False)

    while KukaiState(kukai.state) != KukaiState.SELECTING_OPEN:
        await kukai_service.proceed(db_session, kukai)

    with pytest.raises(InvalidStateError):
        await kukai_service.edit_kukai(db_session, kukai, submission_min=2)


@pytest.mark.asyncio
async def test_edit_kukai_can_set_submission_max_unlimited(db_session):
    kukai = await _make_kukai(db_session)
    assert kukai.submission_max == 3

    await kukai_service.edit_kukai(
        db_session,
        kukai,
        submission_max_unlimited=True,
    )

    assert kukai.submission_max is None


@pytest.mark.asyncio
async def test_add_and_remove_kukai_admin(db_session):
    kukai = await _make_kukai(db_session)

    await kukai_service.add_kukai_admin(
        db_session,
        kukai,
        user_id=2000,
        added_by=100,
    )
    assert await kukai_repo.is_admin(db_session, kukai.id, 2000) is True

    await kukai_service.remove_kukai_admin(
        db_session,
        kukai,
        user_id=2000,
    )
    assert await kukai_repo.is_admin(db_session, kukai.id, 2000) is False


@pytest.mark.asyncio
async def test_export_and_import_payload_roundtrip(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=True, entry_approval=True)
    await db_session.flush()

    await entry_service.enter(db_session, kukai, user_id=101, haigo="甲")
    await entry_service.approve(db_session, kukai, approver_id=100, target_user_id=101)
    await kukai_service.proceed(db_session, kukai)  # entry_closed
    await kukai_service.proceed(db_session, kukai)  # submission_open

    sub, _ = await submission_service.submit(db_session, kukai, user_id=101, text="春の海")
    await kukai_service.proceed(db_session, kukai)  # submission_closed
    await kukai_service.proceed(db_session, kukai)  # waiting_publish
    published = await submission_service.publish(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)  # selecting_open
    await select_service.cast_select(
        db_session,
        kukai,
        selector_user_id=202,
        submission_id=sub.id,
        select_label_id=kukai.select_labels[0].id,
        comment="良い句",
    )
    await kukai_service.proceed(db_session, kukai)  # selecting_closed

    payload = await export_service.export_payload(db_session, guild_id=1, kukai_id=kukai.id)
    assert payload["kukai_count"] == 1
    first = payload["kukais"][0]
    assert first["kukai"]["title"] == "Phase9テスト句会"
    assert len(first["entries"]) == 1
    assert len(first["submissions"]) == 1
    assert len(first["published_submissions"]) == 1
    assert len(first["selects"]) == 1
    assert len(first["select_comments"]) == 1
    assert len(first["results"]) == 1

    imported_ids = await export_service.import_payload(db_session, guild_id=1, payload=payload)
    assert len(imported_ids) == 1
    imported_kukai_id = imported_ids[0]
    assert imported_kukai_id != kukai.id

    admin_count = (
        await db_session.execute(
            select(KukaiAdmin).where(KukaiAdmin.kukai_id == imported_kukai_id)
        )
    ).scalars().all()
    entry_count = (
        await db_session.execute(select(Entry).where(Entry.kukai_id == imported_kukai_id))
    ).scalars().all()
    submission_count = (
        await db_session.execute(select(Submission).where(Submission.kukai_id == imported_kukai_id))
    ).scalars().all()
    published_count = (
        await db_session.execute(
            select(PublishedSubmission).where(PublishedSubmission.kukai_id == imported_kukai_id)
        )
    ).scalars().all()
    select_count = (
        await db_session.execute(select(Select).where(Select.kukai_id == imported_kukai_id))
    ).scalars().all()

    assert len(admin_count) == 0
    assert len(entry_count) == 1
    assert len(submission_count) == 1
    assert len(published_count) == 1
    assert len(select_count) == 1
    assert published[0].number == 1
