"""Admin-only kukai notices, backed by a private thread when possible."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.kukai import KukaiAdmin
from bot.utils.discord_retry import send_with_retry
from bot.utils.embed_builder import COLOR_INFO

logger = logging.getLogger(__name__)


async def admin_user_ids(session: AsyncSession, kukai) -> list[int]:
    result = await session.execute(select(KukaiAdmin.user_id).where(KukaiAdmin.kukai_id == kukai.id))
    return list(dict.fromkeys([kukai.created_by, *result.scalars().all()]))


async def send_admin_notice(
    bot,
    session: AsyncSession,
    kukai,
    *,
    title: str,
    description: str,
    fields: Iterable[tuple[str, str]] = (),
    mention_admins: bool = False,
    view: discord.ui.View | None = None,
) -> bool:
    """Send a notice to the kukai private admin thread, creating it if needed."""
    guild = bot.get_guild(kukai.guild_id) if bot is not None else None
    if guild is None:
        return False

    embed = discord.Embed(title=title, description=description, color=COLOR_INFO)
    for name, value in fields:
        embed.add_field(name=name, value=_limited(value), inline=False)
    embed.set_footer(text=f"句会ID: {kukai.id}")

    admin_ids = await admin_user_ids(session, kukai)
    thread = await ensure_admin_thread(bot, session, kukai, admin_ids=admin_ids)
    content = " ".join(f"<@{user_id}>" for user_id in admin_ids) if mention_admins else None

    if thread is not None and hasattr(thread, "send"):
        try:
            await send_with_retry(
                lambda: thread.send(
                    content=content,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
            )
            return True
        except (discord.Forbidden, discord.HTTPException) as error:
            logger.warning("admin thread notice failed (kukai_id=%s): %s", kukai.id, error)

    return await _fallback_admin_notice(guild, kukai, admin_ids, content=content, embed=embed, view=view)


async def ensure_admin_thread(bot, session: AsyncSession, kukai, *, admin_ids: list[int] | None = None):
    """Return the private admin thread, creating it under the kukai channel if missing."""
    guild = bot.get_guild(kukai.guild_id) if bot is not None else None
    if guild is None:
        return None

    thread = _get_existing_thread(bot, guild, getattr(kukai, "admin_thread_id", None))
    if thread is not None:
        await _sync_thread_members(thread, guild, admin_ids or await admin_user_ids(session, kukai))
        return thread

    if not kukai.channel_id:
        return None
    parent = guild.get_channel(kukai.channel_id)
    if parent is None or not hasattr(parent, "create_thread"):
        return None

    try:
        thread = await parent.create_thread(
            name=_thread_name(kukai),
            type=discord.ChannelType.private_thread,
            invitable=False,
            auto_archive_duration=10080,
            reason="Kukai admin notices",
        )
    except TypeError:
        try:
            thread = await parent.create_thread(
                name=_thread_name(kukai),
                type=discord.ChannelType.private_thread,
                auto_archive_duration=10080,
                reason="Kukai admin notices",
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            logger.warning("admin thread creation failed (kukai_id=%s): %s", kukai.id, error)
            return None
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.warning("admin thread creation failed (kukai_id=%s): %s", kukai.id, error)
        return None

    kukai.admin_thread_id = thread.id
    await session.flush()
    await _sync_thread_members(thread, guild, admin_ids or await admin_user_ids(session, kukai))
    return thread


def _get_existing_thread(bot, guild, thread_id: int | None):
    if not thread_id:
        return None
    thread = None
    if hasattr(bot, "get_channel"):
        thread = bot.get_channel(thread_id)
    if thread is None and hasattr(guild, "get_thread"):
        thread = guild.get_thread(thread_id)
    return thread


async def _sync_thread_members(thread, guild, admin_ids: list[int]) -> None:
    for user_id in admin_ids:
        member = guild.get_member(user_id)
        if member is None or not hasattr(thread, "add_user"):
            continue
        try:
            await thread.add_user(member)
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("failed to add admin %s to admin thread %s", user_id, getattr(thread, "id", None))


async def _fallback_admin_notice(
    guild,
    kukai,
    admin_ids: list[int],
    *,
    content: str | None,
    embed,
    view: discord.ui.View | None = None,
) -> bool:
    creator = guild.get_member(kukai.created_by)
    if creator is not None:
        try:
            await send_with_retry(lambda: creator.send(embed=embed, view=view))
            return True
        except Exception as error:
            logger.warning("admin notice creator DM failed (kukai_id=%s): %s", kukai.id, error)

    if kukai.channel_id:
        channel = guild.get_channel(kukai.channel_id)
        if channel is not None and hasattr(channel, "send"):
            try:
                await send_with_retry(
                    lambda: channel.send(
                        content=content,
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                    )
                )
                return True
            except Exception as error:
                logger.error("admin notice channel fallback failed (kukai_id=%s): %s", kukai.id, error)
    return False


def _thread_name(kukai) -> str:
    base = re.sub(r"[\\/#@:]", "-", kukai.title).strip() or "kukai"
    return f"管理-{kukai.id}-{base}"[:100]


def _limited(value: str, *, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 20] + "\n...（省略）"
