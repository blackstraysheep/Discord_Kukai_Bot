"""Helpers for building standard Discord embeds."""

import discord

from bot.utils.datetime_utils import format_jst

# Colour palette
COLOR_INFO = discord.Color.blue()
COLOR_SUCCESS = discord.Color.green()
COLOR_WARNING = discord.Color.orange()
COLOR_ERROR = discord.Color.red()
COLOR_RESULT = discord.Color.gold()


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
    if kukai.voting_close_at:
        embed.add_field(name="選句締切", value=format_jst(kukai.voting_close_at), inline=False)

    embed.set_footer(text=f"句会 ID: {kukai.id}")
    return embed
