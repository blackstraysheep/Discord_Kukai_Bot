from types import SimpleNamespace

import pytest

import bot.cogs.kukai_cog as kukai_cog
from bot.cogs.kukai_cog import StageActionView, stage_action_custom_id
from bot.cogs.entry_cog import EntryHaigoModal
from bot.cogs.result_cog import ResultOpenView, result_open_custom_id
from bot.state_machine.states import KukaiState
from bot.ui.persistent_views import _register_views_for_kukais
from bot.utils.stage_announcement import build_action_button_message


class _FakeBot:
    def __init__(self) -> None:
        self.views = []

    def add_view(self, view) -> None:
        self.views.append(view)


def _custom_ids(view) -> list[str]:
    return [item.custom_id for item in view.children]


class _FakeResponse:
    def __init__(self) -> None:
        self.modal = None

    async def send_modal(self, modal) -> None:
        self.modal = modal


def test_stage_action_view_is_persistent_with_stable_custom_id():
    view = StageActionView(123, KukaiState.SUBMISSION_OPEN)

    assert view.timeout is None
    assert _custom_ids(view) == [
        stage_action_custom_id(123, KukaiState.SUBMISSION_OPEN),
    ]


def test_result_open_view_is_persistent_with_stable_custom_id():
    view = ResultOpenView(123, initial_format="number")

    assert view.timeout is None
    assert _custom_ids(view) == [
        result_open_custom_id(123, "number"),
    ]


def test_register_views_adds_stage_buttons_and_result_buttons_for_result_kukai():
    bot = _FakeBot()
    kukais = [
        SimpleNamespace(id=1, state=KukaiState.SUBMISSION_OPEN.value, result_message_id=None),
        SimpleNamespace(id=2, state=KukaiState.RESULTS.value, result_message_id=999),
    ]

    count = _register_views_for_kukais(bot, kukais)

    assert count == 10
    custom_ids = [item.custom_id for view in bot.views for item in view.children]
    assert "kukai:stage:1:entry_open" in custom_ids
    assert "kukai:stage:1:submission_open" in custom_ids
    assert "kukai:stage:1:selecting_open" in custom_ids
    assert "kukai:result:2:default" in custom_ids
    assert "kukai:result:2:score" in custom_ids
    assert "kukai:result:2:number" in custom_ids
    assert "kukai:result:2:author" in custom_ids


@pytest.mark.asyncio
async def test_entry_stage_button_sends_modal_before_database_lookup(monkeypatch):
    def fail_get_session():
        raise AssertionError("entry modal should be sent before database lookup")

    monkeypatch.setattr(kukai_cog, "get_session", fail_get_session)
    response = _FakeResponse()
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=456),
        channel=None,
        channel_id=789,
        response=response,
    )
    view = StageActionView(123, KukaiState.ENTRY_OPEN)

    await view.children[0].callback(interaction)

    assert isinstance(response.modal, EntryHaigoModal)
    assert response.modal.kukai_id == 123
    assert response.modal.guild_id == 456
    assert response.modal.channel_id == 789


@pytest.mark.asyncio
async def test_manual_result_button_message_uses_persistent_result_view():
    kukai = SimpleNamespace(
        id=123,
        title="結果句会",
        author_reveal=False,
        author_publication_mode="with_result",
        points_enabled=True,
        result_display_default="score",
    )

    embed, view, error = await build_action_button_message(kukai, "result", result_count=5)

    assert error is None
    assert embed.footer.text.endswith("全 5 句")
    assert isinstance(view, ResultOpenView)
    assert view.children[0].label == "結果を見る"
    assert view.children[0].custom_id == result_open_custom_id(123, "score")
