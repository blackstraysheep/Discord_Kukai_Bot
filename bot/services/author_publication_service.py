"""Shared manual author-publication workflow."""

from __future__ import annotations

import logging

import discord

from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.utils.discord_retry import send_with_retry
from bot.utils.embed_builder import COLOR_INFO

logger = logging.getLogger(__name__)


def reveal_authors(kukai) -> bool:
    state = KukaiState.from_value(kukai.state)
    if state not in {KukaiState.RESULTS, KukaiState.ENDED}:
        raise ServiceError("作者公開は結果公開後に実行できます。")
    mode = getattr(kukai, "author_publication_mode", "with_result")
    if mode == "never":
        raise ServiceError("この句会は「作者公開はしない」に設定されています。")
    if kukai.author_reveal:
        return False
    kukai.author_reveal = True
    return True


async def announce_authors_revealed(guild: discord.Guild, kukai) -> None:
    if not kukai.channel_id:
        return
    channel = guild.get_channel(kukai.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(
        description=f"句会「**{kukai.title}**」の作者を公開しました。",
        color=COLOR_INFO,
    )
    embed.set_footer(text=f"句会ID: {kukai.id}")
    try:
        await send_with_retry(lambda: channel.send(embed=embed))
    except Exception:
        logger.exception("Failed to announce author reveal")
