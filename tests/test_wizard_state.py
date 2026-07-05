"""Tests for kukai creation wizard state defaults."""

from datetime import datetime, timezone

from bot.services import select_rule_service
from bot.ui.wizard import step_basic
from bot.ui.wizard.step_schedule import StepScheduleModal, _placeholder_datetime
from bot.ui.wizard.wizard_state import WizardState


def test_wizard_submission_max_defaults_to_five():
    state = WizardState(user_id=1, guild_id=1)

    assert state.submission_max == 5
    assert state.channel_visibility_policy == "public"


def test_default_select_comment_modes_are_optional_for_select_labels():
    specs = select_rule_service.default_kukai_specs()
    non_author = [
        row for row in specs
        if row["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
    ]

    assert non_author
    assert {row["comment_mode"] for row in non_author} == {"optional"}


def test_schedule_placeholder_datetime_is_future_parseable():
    value = _placeholder_datetime(days_from_now=7, hour=23, minute=59)
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

    assert parsed > datetime.now(timezone.utc)


def test_schedule_modal_prefills_deadline_defaults():
    state = WizardState(user_id=1, guild_id=1, entry_enabled=True)

    modal = StepScheduleModal(state)

    assert modal.entry_close is not None
    assert modal.entry_close.default
    assert modal.submission_close.default
    assert modal.selecting_close.default


def test_basic_step_view_fits_discord_rows_for_new_channel():
    state = WizardState(user_id=1, guild_id=1, title="テスト句会")

    embed, view = step_basic.build(state)

    assert embed.title
    assert view.children


def test_basic_step_view_fits_discord_rows_for_existing_channel():
    state = WizardState(
        user_id=1,
        guild_id=1,
        title="テスト句会",
        use_existing_channel=True,
        existing_channel_id=123,
    )

    embed, view = step_basic.build(state)

    assert embed.title
    assert view.children


def test_existing_channel_with_closed_visibility_cannot_confirm():
    state = WizardState(
        user_id=1,
        guild_id=1,
        title="テスト句会",
        use_existing_channel=True,
        existing_channel_id=123,
        channel_visibility_policy="public_until_participation_close",
    )

    assert state.can_confirm is False
