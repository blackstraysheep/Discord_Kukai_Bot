"""Helpers for building standard Discord embeds."""

from __future__ import annotations

from typing import Any

import discord

from bot.utils.datetime_utils import format_jst

# Colour palette
COLOR_INFO = discord.Color.blue()
COLOR_SUCCESS = discord.Color.green()
COLOR_WARNING = discord.Color.orange()
COLOR_ERROR = discord.Color.red()
COLOR_RESULT = discord.Color.gold()


_AUTHOR_LABEL = "作者コメント"


def build_select_summary(
    submission_min: int,
    submission_max: int | None,
    select_labels: list[Any],
    *,
    override_text: str | None = None,
) -> str:
    """Generate a compact summary string like '3～5句出し、特1並2～3'.

    Each element of `select_labels` may be a SelectLabel ORM instance or a
    plain dict; both expose `.label`/`.min_count`/`.max_count`/`.rank_priority`
    (or equivalent dict keys).

    Labels where min_count == 0 are considered optional and omitted.
    'override_text', if set, is returned as-is (preset custom text).
    """
    if override_text:
        return override_text

    # Submission part
    if submission_max is None:
        sub_part = f"{submission_min}句以上出し"
    elif submission_min == submission_max:
        sub_part = f"{submission_min}句出し"
    else:
        sub_part = f"{submission_min}～{submission_max}句出し"

    def _attr(lbl: Any, key: str, default: Any = None) -> Any:
        if isinstance(lbl, dict):
            return lbl.get(key, default)
        return getattr(lbl, key, default)

    # Show labels that have any count constraint (required OR capped)
    # Skip: min_count==0 AND max_count is None (truly unlimited optional)
    required = [
        lbl for lbl in select_labels
        if (_attr(lbl, "min_count", 0) > 0 or _attr(lbl, "max_count", None) is not None)
        and _attr(lbl, "label", "") != _AUTHOR_LABEL
    ]
    required.sort(key=lambda lbl: _attr(lbl, "rank_priority", 999))

    if not required:
        return sub_part

    used_prefixes: set[str] = set()
    parts: list[str] = []
    for lbl in required:
        name: str = _attr(lbl, "label", "")
        min_c: int = _attr(lbl, "min_count", 0)
        max_c: int | None = _attr(lbl, "max_count", None)

        n = 1
        while n < len(name):
            prefix = name[:n]
            if prefix not in used_prefixes:
                break
            n += 1
        prefix = name[:n]
        used_prefixes.add(prefix)

        if max_c is None:
            count_str = f"{min_c}～"
        elif min_c == max_c:
            count_str = str(min_c)
        else:
            count_str = f"{min_c}～{max_c}"

        parts.append(f"{prefix}{count_str}")

    return f"{sub_part}、{''.join(parts)}"


def error_embed(message: str, title: str = "エラー") -> discord.Embed:
    return discord.Embed(title=f"❌ {title}", description=message, color=COLOR_ERROR)


def success_embed(message: str, title: str = "完了") -> discord.Embed:
    return discord.Embed(title=f"✅ {title}", description=message, color=COLOR_SUCCESS)


def info_embed(message: str, title: str = "") -> discord.Embed:
    return discord.Embed(title=title, description=message, color=COLOR_INFO)


def kukai_info_embed(kukai) -> discord.Embed:
    """Build a summary embed for a kukai."""
    embed = discord.Embed(
        title=kukai.title,
        description=kukai.description or "",
        color=COLOR_INFO,
    )
    if kukai.theme:
        embed.add_field(name="題", value=kukai.theme, inline=True)
    embed.add_field(name="状態", value=kukai.state, inline=True)

    if kukai.submission_close_at:
        embed.add_field(name="投句締切", value=format_jst(kukai.submission_close_at), inline=False)
    if kukai.selecting_close_at:
        embed.add_field(name="選句締切", value=format_jst(kukai.selecting_close_at), inline=False)

    embed.set_footer(text=f"句会 ID: {kukai.id}")
    return embed
