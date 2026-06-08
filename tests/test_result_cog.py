from types import SimpleNamespace

from bot.cogs.result_cog import _available_formats, _resolve_initial_format
from bot.cogs.pdf_cog import (
    _can_show_pdf_author,
    _can_show_result_author,
    _result_pdf_requires_admin,
)
from bot.state_machine.states import KukaiState


def _kukai(*, points_enabled: bool, author_reveal: bool, result_display_default: str = "score"):
    return SimpleNamespace(
        points_enabled=points_enabled,
        author_reveal=author_reveal,
        result_display_default=result_display_default,
    )


def test_available_formats_all_enabled():
    kukai = _kukai(points_enabled=True, author_reveal=True)
    assert _available_formats(kukai) == ["score", "number", "author"]


def test_available_formats_points_off():
    kukai = _kukai(points_enabled=False, author_reveal=True)
    assert _available_formats(kukai) == ["number", "author"]


def test_resolve_initial_format_prefers_requested():
    kukai = _kukai(points_enabled=True, author_reveal=True, result_display_default="number")
    assert _resolve_initial_format(kukai, "author") == "author"


def test_resolve_initial_format_uses_kukai_default():
    kukai = _kukai(points_enabled=True, author_reveal=True, result_display_default="number")
    assert _resolve_initial_format(kukai, None) == "number"


def test_resolve_initial_format_fallback_to_first_available():
    kukai = _kukai(points_enabled=False, author_reveal=False, result_display_default="score")
    assert _resolve_initial_format(kukai, None) == "number"


def test_result_pdf_requires_admin_before_public_results():
    assert _result_pdf_requires_admin(KukaiState.SELECTING_CLOSED) is True
    assert _result_pdf_requires_admin(KukaiState.RESULTS) is False
    assert _result_pdf_requires_admin(KukaiState.ENDED) is False


def test_result_pdf_author_visibility_follows_reveal_flag():
    assert _can_show_result_author(SimpleNamespace(author_reveal=False), True) is False
    assert _can_show_result_author(SimpleNamespace(author_reveal=True), True) is True
    assert _can_show_result_author(SimpleNamespace(author_reveal=True), False) is False


def test_submission_pdf_author_visibility_requires_results_and_reveal_flag():
    kukai = SimpleNamespace(author_reveal=True)
    assert _can_show_pdf_author(kukai, True, state=KukaiState.RESULTS) is True
    assert _can_show_pdf_author(kukai, True, state=KukaiState.SELECTING_CLOSED) is False
    assert _can_show_pdf_author(SimpleNamespace(author_reveal=False), True, state=KukaiState.RESULTS) is False
