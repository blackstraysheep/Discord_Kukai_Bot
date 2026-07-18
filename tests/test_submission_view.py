import pytest

from bot.services.submission_service import DuplicateSubmissionWarning
from bot.ui.submission_view import (
    DuplicateSubmissionNotice,
    _duplicate_warning_text,
    parse_bulk_submission_lines,
    validate_submission_total,
)


def test_parse_bulk_submission_lines_uses_non_empty_lines():
    poems = parse_bulk_submission_lines("  春の句  \n\n夏の句\n", remaining_limit=None)

    assert poems == ["春の句", "夏の句"]


def test_parse_bulk_submission_lines_rejects_empty_text():
    with pytest.raises(ValueError, match="少なくとも1句"):
        parse_bulk_submission_lines(" \n\n", remaining_limit=None)


def test_parse_bulk_submission_lines_allows_empty_text_for_full_edit():
    poems = parse_bulk_submission_lines(" \n\n", remaining_limit=None, allow_empty=True)

    assert poems == []


def test_parse_bulk_submission_lines_rejects_submission_limit_overflow():
    with pytest.raises(ValueError, match="投句上限を超えています（残り2句、1句超過）"):
        parse_bulk_submission_lines("一句\n二句\n三句", remaining_limit=2)


def test_parse_bulk_submission_lines_allows_many_lines_when_submission_limit_is_none():
    poems = parse_bulk_submission_lines("\n".join(f"{index}句" for index in range(20)), remaining_limit=None)

    assert len(poems) == 20


def test_parse_bulk_submission_lines_rejects_too_long_line():
    with pytest.raises(ValueError, match="1行目: 1句は500文字までです。"):
        parse_bulk_submission_lines("あ" * 501, remaining_limit=None)


def test_validate_submission_total_rejects_final_total_overflow():
    with pytest.raises(ValueError, match="投句上限を超えています（上限2句、1句超過）"):
        validate_submission_total(["一句", "二句", "三句"], submission_max=2)


def test_validate_submission_total_allows_unlimited_total():
    validate_submission_total(["一句", "二句", "三句"], submission_max=None)


def test_duplicate_warning_text_uses_current_number_and_guild_name():
    text = _duplicate_warning_text(
        [
            DuplicateSubmissionNotice(
                current_number=2,
                warning=DuplicateSubmissionWarning(
                    submission_id=123,
                    text="春の海",
                    kukai_id=10,
                    title="第一句会",
                    guild_id=999,
                    channel_id=None,
                    result_message_id=None,
                    published_number=7,
                    haigo="春風",
                ),
            )
        ],
        guild_names={999: "俳句サロン"},
    )

    assert "2番「春の海」" in text
    assert "サーバ: 俳句サロン" in text
    assert "サーバID" not in text
    assert "No.7" not in text
