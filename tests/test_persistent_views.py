from types import SimpleNamespace

from bot.cogs.kukai_cog import StageActionView, stage_action_custom_id
from bot.cogs.result_cog import ResultOpenView, result_open_custom_id
from bot.state_machine.states import KukaiState
from bot.ui.persistent_views import _register_views_for_kukais


class _FakeBot:
    def __init__(self) -> None:
        self.views = []

    def add_view(self, view) -> None:
        self.views.append(view)


def _custom_ids(view) -> list[str]:
    return [item.custom_id for item in view.children]


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
