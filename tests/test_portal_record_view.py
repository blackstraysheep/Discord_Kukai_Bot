from types import SimpleNamespace

import discord
import pytest

from bot.ui.participation_record_view import ParticipationRecordOptionsView, parse_record_limit


class _Guild:
    id = 10
    name = "test"

    def get_member(self, user_id):
        return None


def _user(user_id=1):
    return SimpleNamespace(id=user_id, name="user", display_name="ユーザー", bot=False)


def test_private_record_options_fix_target_to_self():
    view = ParticipationRecordOptionsView(
        bot=SimpleNamespace(),
        guild=_Guild(),  # type: ignore[arg-type]
        user=_user(),  # type: ignore[arg-type]
        allow_other=False,
    )

    assert view.is_self is True
    assert not any(isinstance(item, discord.ui.UserSelect) for item in view.children)
    assert [item.row for item in view.children] == [1, 2, 3, 4, 4]
    assert view.limit is None
    assert "要約件数: **全件**" in view.build_embed().description


def test_public_record_options_include_user_selector_and_self_choices():
    view = ParticipationRecordOptionsView(
        bot=SimpleNamespace(),
        guild=_Guild(),  # type: ignore[arg-type]
        user=_user(),  # type: ignore[arg-type]
        allow_other=True,
    )

    assert isinstance(view.children[0], discord.ui.UserSelect)
    scope = view.children[1]
    group = view.children[2]
    assert [option.value for option in scope.options] == ["current", "all"]  # type: ignore[attr-defined]
    assert [option.value for option in group.options] == ["kukai", "server", "haigo"]  # type: ignore[attr-defined]


def test_other_target_removes_cross_server_and_server_grouping():
    view = ParticipationRecordOptionsView(
        bot=SimpleNamespace(),
        guild=_Guild(),  # type: ignore[arg-type]
        user=_user(),  # type: ignore[arg-type]
        allow_other=True,
    )
    view.target = _user(2)  # type: ignore[assignment]
    view.scope = "current"
    view.group_by = "kukai"
    view._rebuild()

    scope = view.children[1]
    group = view.children[2]
    assert [option.value for option in scope.options] == ["current"]  # type: ignore[attr-defined]
    assert [option.value for option in group.options] == ["kukai", "haigo"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", None), ("   ", None), ("1", 1), ("25", 25), ("1000", 1000)],
)
def test_parse_record_limit_accepts_positive_integer_or_blank(raw, expected):
    assert parse_record_limit(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "abc"])
def test_parse_record_limit_rejects_invalid_values(raw):
    with pytest.raises(ValueError, match="1以上の整数"):
        parse_record_limit(raw)
