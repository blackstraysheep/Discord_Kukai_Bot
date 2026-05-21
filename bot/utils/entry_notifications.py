"""Discord notifications for entry lifecycle events."""

from __future__ import annotations

import logging

import discord

from bot.utils.discord_retry import send_with_retry
from bot.utils.embed_builder import COLOR_INFO, COLOR_SUCCESS

logger = logging.getLogger(__name__)


async def notify_entry_status_changed(
    guild: discord.Guild,
    kukai,
    *,
    display_name: str,
    approved: bool,
) -> None:
    """Notify the kukai channel about an entry approval/rejection without mentions."""
    if not kukai.channel_id:
        return

    channel = guild.get_channel(kukai.channel_id)
    if not channel or not hasattr(channel, "send"):
        return

    verb = "承認" if approved else "却下"
    embed = discord.Embed(
        title=f"エントリー{verb}",
        description=f"［**{display_name}**］さんの参加が{verb}されました。",
        color=COLOR_SUCCESS if approved else COLOR_INFO,
    )
    embed.set_footer(text=f"句会ID: {kukai.id}")

    try:
        await send_with_retry(
            lambda: channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.warning("entry status notification failed: %s", error)


async def notify_entries_approved(
    guild: discord.Guild,
    kukai,
    *,
    display_names: list[str],
) -> None:
    """Notify the kukai channel about a batch approval without mentions."""
    if not display_names or not kukai.channel_id:
        return

    channel = guild.get_channel(kukai.channel_id)
    if not channel or not hasattr(channel, "send"):
        return

    lines = [f"［**{name}**］さん" for name in display_names[:30]]
    if len(display_names) > 30:
        lines.append(f"...他 {len(display_names) - 30} 件")

    embed = discord.Embed(
        title="エントリー一括承認",
        description="以下の参加が承認されました。\n" + "\n".join(lines),
        color=COLOR_SUCCESS,
    )
    embed.set_footer(text=f"句会ID: {kukai.id}")

    try:
        await send_with_retry(
            lambda: channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.warning("entry bulk approval notification failed: %s", error)


async def notify_entry_approved(
    guild: discord.Guild,
    kukai,
    *,
    user_id: int,
    display_name: str,
) -> None:
    """Backward-compatible wrapper for approval notifications."""
    _ = user_id
    await notify_entry_status_changed(
        guild,
        kukai,
        display_name=display_name,
        approved=True,
    )


async def notify_entry_rejected(
    guild: discord.Guild,
    kukai,
    *,
    display_name: str,
) -> None:
    await notify_entry_status_changed(
        guild,
        kukai,
        display_name=display_name,
        approved=False,
    )
