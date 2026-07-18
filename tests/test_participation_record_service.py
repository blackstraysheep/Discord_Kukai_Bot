"""Tests for participation record history service."""

from datetime import datetime, timedelta, timezone

import pytest

from bot.formatters.participation_record_markdown_exporter import (
    build_participation_record_markdown,
)
from bot.models.guild_settings import GuildSettings
from bot.services import kukai_service, participation_record_service, submission_service
from bot.services.errors import PermissionError
from bot.state_machine.states import KukaiState
from bot.ui.submission_view import sync_submission_lines


def _utc(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


async def _make_open_kukai(session, *, guild_id: int, title: str):
    kukai = await kukai_service.create_kukai(
        session,
        guild_id=guild_id,
        created_by=100,
        channel_id=200 + guild_id,
        title=title,
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=False,
    )
    while KukaiState(kukai.state) != KukaiState.SUBMISSION_OPEN:
        await kukai_service.proceed(session, kukai)
    return kukai


@pytest.mark.asyncio
async def test_record_me_scope_all_returns_cross_guild_history(db_session):
    first = await _make_open_kukai(db_session, guild_id=1, title="第一句会")
    second = await _make_open_kukai(db_session, guild_id=2, title="第二句会")
    await submission_service.submit(db_session, first, user_id=10, text="春の海", haigo="春風")
    await submission_service.submit(db_session, second, user_id=10, text="夏の川", haigo="青嵐")

    result = await participation_record_service.get_participation_records(
        db_session,
        current_guild_id=1,
        target_user_id=10,
        target_display_name="user10",
        viewer_user_id=10,
        scope="all",
        group_by="haigo",
    )

    assert result.total_kukai_count == 2
    assert result.submission_count == 2
    assert {record.participant_haigo for record in result.records} == {"春風", "青嵐"}
    markdown = build_participation_record_markdown(
        result,
        guild_names={1: "一番サーバ", 2: "二番サーバ"},
    )
    assert "## 春風" in markdown
    assert "春の海" in markdown
    assert "二番サーバ" in markdown


@pytest.mark.asyncio
async def test_record_user_requires_guild_public(db_session):
    kukai = await _make_open_kukai(db_session, guild_id=1, title="第一句会")
    await submission_service.submit(db_session, kukai, user_id=10, text="春の海", haigo="春風")
    kukai.state = KukaiState.RESULTS.value

    with pytest.raises(PermissionError):
        await participation_record_service.get_participation_records(
            db_session,
            current_guild_id=1,
            target_user_id=10,
            target_display_name="user10",
            viewer_user_id=99,
            scope="current",
            group_by="kukai",
        )

    db_session.add(
        GuildSettings(
            guild_id=1,
            participation_record_visibility="guild_public",
        )
    )
    await db_session.flush()

    result = await participation_record_service.get_participation_records(
        db_session,
        current_guild_id=1,
        target_user_id=10,
        target_display_name="user10",
        viewer_user_id=99,
        scope="current",
        group_by="kukai",
    )
    assert result.total_kukai_count == 1


@pytest.mark.asyncio
async def test_sync_submission_lines_warns_duplicate_on_edit(db_session):
    first = await _make_open_kukai(db_session, guild_id=1, title="第一句会")
    await submission_service.submit(db_session, first, user_id=10, text="春の海", haigo="春風")

    second = await _make_open_kukai(db_session, guild_id=1, title="第二句会")
    await submission_service.submit(db_session, second, user_id=10, text="夏の川", haigo="春風")
    current_subs = await submission_service.list_user_submissions(db_session, second.id, 10)

    subs, warnings = await sync_submission_lines(
        db_session,
        second,
        10,
        current_subs,
        ["春の海"],
        haigo="春風",
    )

    assert subs[0].text == "春の海"
    assert warnings
    assert warnings[0].current_number == 1
    assert warnings[0].warning.title == "第一句会"
    assert warnings[0].warning.text == "春の海"
