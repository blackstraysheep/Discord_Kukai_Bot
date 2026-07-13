import pytest

from bot.models.entry import Entry
from bot.models.kukai import Kukai
from bot.models.submission import Submission
from bot.services import check_service
from bot.state_machine.states import KukaiState


def _kukai(*, guild_id=1, title="句会", state=KukaiState.SUBMISSION_OPEN.value, created_by=999):
    return Kukai(
        guild_id=guild_id,
        channel_id=123,
        title=title,
        state=state,
        created_by=created_by,
        entry_enabled=True,
        entry_approval=False,
        submission_min=1,
        submission_max=None,
    )


def test_paginate_lines_keeps_overflow_lines_on_later_pages():
    lines = ["見出し"] + [f"{i}: {'あ' * 30}" for i in range(20)]

    pages = check_service._paginate_lines(lines, limit=120)

    assert len(pages) > 1
    joined = "\n".join(pages)
    assert "見出し" in joined
    assert "0: " in joined
    assert "19: " in joined
    assert all(len(page) <= 120 for page in pages)


@pytest.mark.asyncio
async def test_list_related_kukais_includes_active_user_related_kukais(db_session):
    related_by_entry = _kukai(title="entry", state=KukaiState.ENTRY_OPEN.value)
    related_by_submission = _kukai(title="submission", state=KukaiState.SUBMISSION_OPEN.value)
    unrelated = _kukai(title="other", state=KukaiState.SUBMISSION_OPEN.value)
    ended = _kukai(title="ended", state=KukaiState.ENDED.value)
    db_session.add_all([related_by_entry, related_by_submission, unrelated, ended])
    await db_session.flush()
    db_session.add_all(
        [
            Entry(kukai_id=related_by_entry.id, user_id=100, status="approved"),
            Submission(kukai_id=related_by_submission.id, user_id=100, text="春の句"),
            Entry(kukai_id=ended.id, user_id=100, status="approved"),
        ]
    )
    await db_session.flush()

    kukais = await check_service.list_related_kukais(db_session, guild_id=1, user_id=100)

    titles = {kukai.title for kukai in kukais}
    assert titles == {"entry", "submission"}


@pytest.mark.asyncio
async def test_build_check_pages_splits_many_submissions_without_dropping_rows(db_session):
    kukai = _kukai(title="many", state=KukaiState.SUBMISSION_OPEN.value)
    db_session.add(kukai)
    await db_session.flush()
    db_session.add_all(
        [
            Submission(
                kukai_id=kukai.id,
                user_id=100,
                text=f"{index:03d} " + "長い句" * 30,
            )
            for index in range(40)
        ]
    )
    await db_session.flush()

    pages = await check_service.build_check_pages(db_session, [kukai], user_id=100)

    assert len(pages) > 1
    text = "\n".join(page.description or "" for page in pages)
    assert "000 " in text
    assert "039 " in text
    assert all(len(page.description or "") <= check_service.CHECK_PAGE_DESCRIPTION_LIMIT for page in pages)
