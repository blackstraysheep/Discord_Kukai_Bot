"""Tests for Natsugumo-style submission ruby markup."""

from __future__ import annotations

import pytest

from bot.utils.submission_markup import (
    SubmissionMarkupError,
    discord_safe_submission_text,
    render_submission_for_discord,
    render_submission_for_tex,
    validate_submission_markup,
)


def test_render_submission_for_discord_converts_ruby():
    text = "｜遠山（とおやま）に日の当たりたる｜枯野（かれの）かな"
    assert render_submission_for_discord(text) == "遠山（とおやま）に日の当たりたる枯野（かれの）かな"


def test_render_submission_for_discord_allows_ascii_base():
    assert render_submission_for_discord("炎天下待ち行列に｜www（草生える）") == "炎天下待ち行列にwww（草生える）"


def test_escaped_pipe_is_literal():
    assert render_submission_for_discord("｜｜（本文としての括弧）") == "｜（本文としての括弧）"


def test_plain_text_is_unchanged():
    assert render_submission_for_discord("春の海（括弧も通常文字）") == "春の海（括弧も通常文字）"


def test_discord_safe_submission_text_escapes_after_rendering():
    assert discord_safe_submission_text("｜@here（よみ） #1") == "@\u200bhere（よみ） \\#1"


def test_render_submission_for_tex_converts_ruby_and_escapes_parts():
    assert render_submission_for_tex("｜A&B（50%）", lambda s: s.replace("&", r"\&").replace("%", r"\%")) == (
        r"\ruby{A\&B}{50\%}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "｜（よみ）",
        "｜親字（）",
        "｜親字（よみ",
        "｜親（字（よみ）",
        "｜親字（よ｜み）",
    ],
)
def test_invalid_markup_raises(text):
    with pytest.raises(SubmissionMarkupError):
        validate_submission_markup(text)
