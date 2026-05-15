"""Unit tests for newline-based bulk command parsing."""

import pytest

from bot.utils.bulk_parser import (
    BulkParseError,
    parse_bool,
    parse_fields,
    parse_label_spec,
    parse_optional_int,
)


def test_parse_fields_ignores_blank_and_comment_lines():
    fields = parse_fields(
        """
        # comment
        title=春の句会

        label=特選,2,1,0,1,none
        """
    )

    assert [(field.key, field.value, field.line_no) for field in fields] == [
        ("title", "春の句会", 3),
        ("label", "特選,2,1,0,1,none", 5),
    ]


def test_parse_fields_rejects_broken_line():
    with pytest.raises(BulkParseError):
        parse_fields("title")


def test_parse_bool_and_unlimited_int():
    assert parse_bool("on") is True
    assert parse_bool("0") is False
    assert parse_optional_int("∞") is None
    assert parse_optional_int("3") == 3


def test_parse_label_spec_with_rank():
    spec = parse_label_spec("特選,2,1,0,1,required")

    assert spec == {
        "label": "特選",
        "point": 2,
        "rank_priority": 1,
        "min_count": 0,
        "max_count": 1,
        "comment_mode": "required",
    }


def test_parse_label_spec_without_rank():
    spec = parse_label_spec("並選,1,0,5,none")

    assert "rank_priority" not in spec
    assert spec["min_count"] == 0
    assert spec["max_count"] == 5
