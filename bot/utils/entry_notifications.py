"""Discord notifications for entry lifecycle events."""

from __future__ import annotations

import logging

import discord

from bot.utils.discord_retry import send_with_retry
from bot.utils.embed_builder import COLOR_SUCCESS

logger = logging.getLogger(__name__)


async def notify_entry_approved(
    guild: discord.Guild,
    kukai,
    *,
    user_id: int,
    display_name: str,
) -> None:
    """Notify only the approved user in the kukai channel."""
    if not kukai.channel_id:
        return

    channel = guild.get_channel(kukai.channel_id)
    if not channel or not hasattr(channel, "send"):
        return

    embed = discord.Embed(
        title="エントリー承認",
        description=f"「**{kukai.title}**」へのエントリーが承認されました。",
        color=COLOR_SUCCESS,
    )
    embed.add_field(name="参加者", value=f"**{display_name}**", inline=False)
    embed.set_footer(text=f"句会ID: {kukai.id}")

    try:
        await send_with_retry(
            lambda: channel.send(
                content=f"<@{user_id}>",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.warning("entry approval notification failed: %s", error)
