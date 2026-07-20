from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from bot.repositories import select_repo, submission_repo
from bot.services import kukai_service, select_lab_service, select_service, submission_service
from bot.services.errors import ValidationError
from bot.state_machine.states import KukaiState
from bot.ui.select_lab import BatchSelectView, ReviewSelectView
from bot.cogs.select_lab_cog import SelectLabCog


def _utc(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


def _label(label_id: int, name: str, *, max_count=None, comment_mode="optional"):
    return SimpleNamespace(
        id=label_id,
        label=name,
        point=1,
        min_count=0,
        max_count=max_count,
        comment_mode=comment_mode,
    )


def _published(number: int, *, user_id: int = 99):
    return SimpleNamespace(
        number=number,
        submission_id=number,
        submission=SimpleNamespace(user_id=user_id, text=f"句 {number}"),
    )


def _fake_data(count=3, labels=None):
    labels = labels or [_label(1, "特選", max_count=1), _label(2, "並選", max_count=5)]
    author = _label(999, "作者コメント", comment_mode="required")
    return select_lab_service.SelectLabData(
        submissions=[_published(number) for number in range(1, count + 1)],
        labels=[*labels, author],
        selects_by_submission={},
        overall_comment="",
    )


async def _setup_selecting(session):
    kukai = await kukai_service.create_kukai(
        session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="Lab句会",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=False,
    )
    while KukaiState(kukai.state) != KukaiState.SUBMISSION_OPEN:
        await kukai_service.proceed(session, kukai)
    await submission_service.submit(session, kukai, user_id=1, text="春の海")
    await submission_service.submit(session, kukai, user_id=3, text="夏の川")
    await submission_service.submit(session, kukai, user_id=2, text="秋の空")
    await kukai_service.proceed(session, kukai)
    await kukai_service.proceed(session, kukai)
    await submission_service.publish(session, kukai)
    await kukai_service.proceed(session, kukai)
    await session.commit()
    return kukai


def test_form_text_escape_round_trip():
    original = "一行目\n二行目\\末尾"
    assert select_lab_service.unescape_form_text(
        select_lab_service.escape_form_text(original)
    ) == original


def test_parse_form_payload_supports_grouped_labels_and_comments():
    payload = select_lab_service.parse_form_payload(
        "特選=3\n並選=1, 4,8",
        r"3=一行目\n二行目=補足",
        "10=作者コメント",
        "総評です",
    )
    assert payload.assignments == {3: "特選", 1: "並選", 4: "並選", 8: "並選"}
    assert payload.comments[3] == "一行目\n二行目=補足"
    assert payload.author_comments == {10: "作者コメント"}
    assert payload.overall_comment == "総評です"


def test_parse_form_payload_rejects_duplicate_submission():
    with pytest.raises(ValidationError, match="複数のラベル"):
        select_lab_service.parse_form_payload("特選=1\n並選=1", "", "", "")


def test_review_uses_label_buttons_up_to_five_and_select_after_that():
    kukai = SimpleNamespace(id=1, title="テスト")
    five = ReviewSelectView(
        kukai,
        _fake_data(labels=[_label(index, f"選{index}") for index in range(1, 6)]),
        2,
    )
    label_buttons = [child for child in five.children if isinstance(child, discord.ui.Button) and child.row == 1]
    assert len(label_buttons) == 5

    six = ReviewSelectView(
        kukai,
        _fake_data(labels=[_label(index, f"選{index}") for index in range(1, 7)]),
        2,
    )
    label_selects = [child for child in six.children if isinstance(child, discord.ui.Select) and child.row == 1]
    assert len(label_selects) == 1
    assert len(label_selects[0].options) == 6


def test_review_next_unprocessed_wraps_and_stays_when_complete():
    data = _fake_data(count=3)
    selected = SimpleNamespace(is_self_comment=False, select_label_id=1)
    data.selects_by_submission = {1: selected, 2: selected}
    assert ReviewSelectView.next_unprocessed_index(data, 2, 1) == 2
    data.selects_by_submission[3] = selected
    assert ReviewSelectView.next_unprocessed_index(data, 2, 1) == 1


def test_batch_pages_are_limited_to_25_options():
    view = BatchSelectView(SimpleNamespace(id=1, title="テスト"), _fake_data(count=26), 2)
    assignment = next(child for child in view.children if isinstance(child, discord.ui.Select) and child.row == 1)
    assert len(assignment.options) == 25
    assert view.page_count == 2


def test_select_lab_exposes_three_independent_subcommands():
    assert {command.name for command in SelectLabCog.select_lab.commands} == {
        "review",
        "batch",
        "form",
    }


@pytest.mark.asyncio
async def test_form_replace_is_visible_through_existing_select_repository(db_session):
    kukai = await _setup_selecting(db_session)
    published = await submission_repo.list_published(db_session, kukai.id)
    normal = [item for item in published if item.submission.user_id != 2]
    own = next(item for item in published if item.submission.user_id == 2)
    labels = [label for label in kukai.select_labels if label.label != "作者コメント"]
    first_label = labels[0]
    first_label.comment_mode = "optional"
    first_label.max_count = 1
    await db_session.flush()

    payload = select_lab_service.SelectFormPayload(
        assignments={normal[0].number: first_label.label},
        comments={normal[0].number: "Labからの選評"},
        author_comments={own.number: "作者コメント"},
        overall_comment="Lab総評",
    )
    await select_lab_service.replace_from_form(db_session, kukai, 2, payload)
    await db_session.flush()

    rows = await select_repo.get_selects_by_selector(db_session, kukai.id, 2)
    assert len(rows) == 2
    normal_row = next(row for row in rows if not row.is_self_comment)
    assert normal_row.comment.comment == "Labからの選評"
    assert (await select_repo.get_overall_comment(db_session, kukai.id, 2)).comment == "Lab総評"

    cleared = select_lab_service.SelectFormPayload({}, {}, {}, "")
    await select_lab_service.replace_from_form(db_session, kukai, 2, cleared)
    await db_session.flush()
    assert await select_repo.get_selects_by_selector(db_session, kukai.id, 2) == []
    assert await select_repo.get_overall_comment(db_session, kukai.id, 2) is None


@pytest.mark.asyncio
async def test_form_validation_does_not_replace_existing_rows(db_session):
    kukai = await _setup_selecting(db_session)
    published = await submission_repo.list_published(db_session, kukai.id)
    target = next(item for item in published if item.submission.user_id != 2)
    label = next(label for label in kukai.select_labels if label.label != "作者コメント")
    await select_service.cast_select(db_session, kukai, 2, target.submission_id, label.id)
    await db_session.flush()

    invalid = select_lab_service.SelectFormPayload(
        assignments={999: label.label}, comments={}, author_comments={}, overall_comment=""
    )
    with pytest.raises(ValidationError, match="No.999"):
        await select_lab_service.replace_from_form(db_session, kukai, 2, invalid)

    rows = await select_repo.get_selects_by_selector(db_session, kukai.id, 2)
    assert len(rows) == 1
    assert rows[0].submission_id == target.submission_id


@pytest.mark.asyncio
async def test_batch_reconcile_only_changes_current_page(db_session):
    kukai = await _setup_selecting(db_session)
    published = [item for item in await submission_repo.list_published(db_session, kukai.id) if item.submission.user_id != 2]
    label = next(label for label in kukai.select_labels if label.label == "並選")
    label.comment_mode = "optional"
    await select_service.cast_select(db_session, kukai, 2, published[1].submission_id, label.id)
    await select_lab_service.reconcile_batch_page(
        db_session,
        kukai,
        2,
        label,
        {published[0].submission_id},
        {published[0].submission_id},
    )
    await db_session.flush()

    rows = await select_repo.get_selects_by_selector(db_session, kukai.id, 2)
    assert {row.submission_id for row in rows} == {
        published[0].submission_id,
        published[1].submission_id,
    }
