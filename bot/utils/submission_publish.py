"""Helpers for rendering numbered submission publication messages."""

from __future__ import annotations

import discord

from bot.utils.embed_builder import COLOR_INFO
from bot.utils.submission_markup import discord_safe_submission_text

_MAX_EMBED_DESCRIPTION = 3900


def build_submission_publish_embeds(kukai, published_submissions) -> list[discord.Embed]:
    """Build paginated embeds for numbered submission list publication."""
    lines: list[str] = []
    for published in published_submissions:
        text = published.submission.text if published.submission else ""
        lines.append(f"`{published.number}.` {discord_safe_submission_text(text)}")

    pages: list[list[str]] = []
    current_page: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_page and current_len + line_len > _MAX_EMBED_DESCRIPTION:
            pages.append(current_page)
            current_page = [line]
            current_len = line_len
        else:
            current_page.append(line)
            current_len += line_len

    if current_page:
        pages.append(current_page)

    page_count = len(pages)
    embeds: list[discord.Embed] = []
    for index, page_lines in enumerate(pages, start=1):
        title = f"📋 {kukai.title} — 投句一覧"
        if page_count > 1:
            title += f" ({index}/{page_count})"
        embed = discord.Embed(
            title=title,
            description="\n".join(page_lines),
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"全 {len(lines)} 句　|　句会 ID: {kukai.id}")
        embeds.append(embed)

    return embeds
