"""Utilities for rendering submission text markup.

The source text is stored as entered.  These helpers validate and render the
Natsugumo-style ruby notation used by submissions:

    ｜親字（よみ）
"""

from __future__ import annotations

from collections.abc import Callable

from bot.utils.text import discord_safe

_PIPE = "｜"
_OPEN = "（"
_CLOSE = "）"


class SubmissionMarkupError(ValueError):
    """Raised when submission markup cannot be parsed safely."""


def validate_submission_markup(text: str) -> None:
    """Validate supported submission markup."""
    _render(text, plain=lambda value: value, ruby=lambda base, reading: base)


def render_submission_for_discord(text: str) -> str:
    """Render submission text for Discord, preserving ruby as parenthesized reading."""
    return _render(text, plain=lambda value: value, ruby=lambda base, reading: f"{base}{_OPEN}{reading}{_CLOSE}")


def discord_safe_submission_text(text: str, *, limit: int | None = None) -> str:
    """Render submission text for Discord and escape Markdown/mentions."""
    rendered = render_submission_for_discord(text)
    if limit is not None:
        rendered = rendered[:limit]
    return discord_safe(rendered)


def render_submission_for_tex(text: str, escape: Callable[[str], str]) -> str:
    """Render submission text for TeX, converting ruby to ``\\ruby`` commands."""
    return _render(
        text,
        plain=escape,
        ruby=lambda base, reading: rf"\ruby{{{escape(base)}}}{{{escape(reading)}}}",
    )


def _render(
    text: str,
    *,
    plain: Callable[[str], str],
    ruby: Callable[[str, str], str],
) -> str:
    parts: list[str] = []
    buffer: list[str] = []
    i = 0
    length = len(text)

    def flush_plain() -> None:
        if buffer:
            parts.append(plain("".join(buffer)))
            buffer.clear()

    while i < length:
        if text[i] != _PIPE:
            buffer.append(text[i])
            i += 1
            continue

        if i + 1 < length and text[i + 1] == _PIPE:
            buffer.append(_PIPE)
            i += 2
            continue

        open_index = text.find(_OPEN, i + 1)
        if open_index == -1:
            raise SubmissionMarkupError("ルビ記法の読み開始「（」が見つかりません。")

        base = text[i + 1:open_index]
        if not base:
            raise SubmissionMarkupError("ルビの親字が空です。")
        if _has_markup_delimiter(base):
            raise SubmissionMarkupError("ルビの親字に「｜」「（」「）」は使えません。")

        close_index = text.find(_CLOSE, open_index + 1)
        if close_index == -1:
            raise SubmissionMarkupError("ルビ記法の読み終了「）」が見つかりません。")

        reading = text[open_index + 1:close_index]
        if not reading:
            raise SubmissionMarkupError("ルビの読みが空です。")
        if _has_markup_delimiter(reading):
            raise SubmissionMarkupError("ルビの読みに「｜」「（」「）」は使えません。")

        flush_plain()
        parts.append(ruby(base, reading))
        i = close_index + 1

    flush_plain()
    return "".join(parts)


def _has_markup_delimiter(value: str) -> bool:
    return any(ch in value for ch in (_PIPE, _OPEN, _CLOSE))
