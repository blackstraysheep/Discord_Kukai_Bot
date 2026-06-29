from types import SimpleNamespace

from bot.ui.select_view import SelectView


def _published_submission(number: int, submission_id: int, *, user_id: int = 999):
    return SimpleNamespace(
        number=number,
        submission_id=submission_id,
        submission=SimpleNamespace(text=f"句 {number}", user_id=user_id),
    )


def _label(label_id: int, label: str):
    return SimpleNamespace(
        id=label_id,
        label=label,
        point=1,
        min_count=0,
        max_count=None,
    )


def _view() -> SelectView:
    return SelectView(
        SimpleNamespace(id=123, title="テスト句会"),
        [_published_submission(1, 10)],
        [_label(1, "特選"), _label(2, "並選")],
        {},
        overall_comment="総評です",
        selector_user_id=456,
    )


def test_select_view_copy_with_overall_does_not_mutate_current_view():
    view = _view()
    original_children = list(view.children)

    next_view = view._copy_with(selected_submission_id=None)

    assert view._selected_submission_id == 10
    assert view.children == original_children
    assert next_view._selected_submission_id is None
    assert next_view._is_overall_selected()


def test_select_view_copy_with_label_does_not_mutate_current_view():
    view = _view()
    original_children = list(view.children)

    next_view = view._copy_with(selected_label_value="2")

    assert view._selected_label_value == "1"
    assert view.children == original_children
    assert next_view._selected_label_value == "2"


def test_submission_select_first_page_reserves_option_slot_for_overall_comment():
    view = SelectView(
        SimpleNamespace(id=123, title="テスト句会"),
        [_published_submission(number, number) for number in range(1, 26)],
        [_label(1, "特選")],
        {},
        overall_comment="",
        selector_user_id=456,
    )

    submission_select = view.children[0]
    option_values = [option.value for option in submission_select.options]

    assert len(option_values) == 25
    assert "__overall__" in option_values
    assert "25" not in option_values


def test_submission_select_later_page_can_select_25th_submission():
    view = SelectView(
        SimpleNamespace(id=123, title="テスト句会"),
        [_published_submission(number, number) for number in range(1, 26)],
        [_label(1, "特選")],
        {},
        overall_comment="",
        selector_user_id=456,
        page_index=1,
    )

    submission_select = view.children[0]
    option_values = [option.value for option in submission_select.options]

    assert len(option_values) == 2
    assert "25" in option_values
    assert "__overall__" in option_values
