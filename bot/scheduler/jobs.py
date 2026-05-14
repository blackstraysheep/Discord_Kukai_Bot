"""APScheduler job functions.

All functions must be importable top-level so APScheduler can persist and
restore them across restarts. They receive only serializable arguments.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)

# Bot reference injected at startup; jobs check this before acting.
_bot: commands.Bot | None = None


def set_bot(bot: commands.Bot) -> None:
    global _bot
    _bot = bot


# ── Notification job ──────────────────────────────────────────────────────

async def notification_job(schedule_id: int) -> None:
    """Fire a stored NotificationSchedule: build and send the reminder message."""
    if _bot is None:
        logger.warning("notification_job: bot not set (schedule_id=%d)", schedule_id)
        return

    await _bot.wait_until_ready()

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from bot.database import get_session
    from bot.models.notification import NotificationLog, NotificationSchedule
    from bot.models.kukai import Kukai
    from bot.utils.discord_retry import send_with_retry
    from bot.utils.embed_builder import COLOR_INFO
    from bot.utils.datetime_utils import format_jst

    async with get_session() as session:
        ns_result = await session.execute(
            select(NotificationSchedule)
            .where(NotificationSchedule.id == schedule_id)
            .options(selectinload(NotificationSchedule.kukai))
        )
        ns = ns_result.scalar_one_or_none()
        if not ns or ns.fired:
            return

        kukai = ns.kukai
        if kukai is None:
            return

        # Build message
        EVENT_JA = {
            "submission_close": "投句締切",
            "selecting_close": "選句締切",
            "entry_close": "エントリー締切",
        }
        event_ja = EVENT_JA.get(ns.event_type, ns.event_type)
        deadline_map = {
            "submission_close": kukai.submission_close_at,
            "selecting_close": kukai.selecting_close_at,
            "entry_close": kukai.entry_close_at if hasattr(kukai, "entry_close_at") else None,
        }
        deadline_dt = deadline_map.get(ns.event_type)
        hours_left = round(ns.offset_secs / 3600)
        time_str = format_jst(deadline_dt) if deadline_dt else "未定"

        embed_desc = (
            f"⏰ 「**{kukai.title}**」の **{event_ja}** まで約 **{hours_left}時間** です。\n"
            f"締切: {time_str}"
        )

        import discord
        embed = discord.Embed(description=embed_desc, color=COLOR_INFO)
        embed.set_footer(text=f"句会 ID: {kukai.id}")

        # Determine channel
        channel_id = ns.channel_id if ns.channel_id else kukai.channel_id
        sent_count = 0
        error_msg = None

        if channel_id and channel_id > 0:
            guild = _bot.get_guild(kukai.guild_id)
            if guild:
                channel = guild.get_channel(channel_id)
                if channel and hasattr(channel, "send"):
                    try:
                        await send_with_retry(lambda: channel.send(embed=embed))
                        sent_count = 1
                    except Exception as e:
                        error_msg = str(e)
                        logger.error("notification_job send failed: %s", e)

        ns.fired = True
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            NotificationLog(
                schedule_id=ns.id,
                sent_at=now,
                target_count=sent_count,
                error=error_msg,
            )
        )


# ── Deadline job ──────────────────────────────────────────────────────────

async def deadline_job(kukai_id: int, event_type: str) -> None:
    """Handle a kukai deadline: auto-advance state based on mode setting."""
    if _bot is None:
        logger.warning("deadline_job: bot not set (kukai_id=%d)", kukai_id)
        return

    await _bot.wait_until_ready()

    from bot.database import get_session
    from bot.services import kukai_service
    from bot.services.errors import ServiceError
    from bot.state_machine.states import KukaiState
    from bot.utils.embed_builder import COLOR_INFO

    import discord

    async with get_session() as session:
        try:
            kukai = await session.get(
                __import__("bot.models.kukai", fromlist=["Kukai"]).Kukai, kukai_id
            )
            if not kukai:
                return

            state = KukaiState.from_value(kukai.state)

            if event_type == "submission_close":
                mode = kukai.submission_mode
                if mode == "manual":
                    return
                if state != KukaiState.SUBMISSION_OPEN:
                    return

                should_advance = False
                if mode == "full_auto":
                    should_advance = True
                elif mode == "semi_auto":
                    should_advance = await _all_submitted(session, kukai)

                if should_advance:
                    await kukai_service.proceed(session, kukai)
                    logger.info(
                        "deadline_job: auto-advanced kukai %d to submission_closed", kukai_id
                    )
                    if kukai.publish_mode == "auto":
                        await _auto_publish_submission_list(session, kukai)
                        await kukai_service.proceed(session, kukai)
                        logger.info(
                            "deadline_job: auto-published and advanced kukai %d to SELECTING_OPEN",
                            kukai_id,
                        )
                        await _notify_channel(
                            _bot, kukai,
                            "投句期間が終了したため、投句一覧を番号付きで公開して選句を開始しました。",
                        )
                    else:
                        await _notify_channel(
                            _bot, kukai,
                            "投句期間が終了しました。次のステップに進んでください。",
                        )
                else:
                    await _notify_admins(
                        _bot, kukai,
                        "投句締切になりましたが、未投句の参加者がいます。"
                        f"\n句会「{kukai.title}」(ID: {kukai.id}) を確認してください。",
                    )

            elif event_type == "selecting_close":
                mode = kukai.selecting_mode
                if mode == "manual":
                    return
                if state != KukaiState.SELECTING_OPEN:
                    return

                should_advance = False
                if mode == "full_auto":
                    should_advance = True
                elif mode == "semi_auto":
                    should_advance = await _all_selected(session, kukai)

                if should_advance:
                    await kukai_service.proceed(session, kukai)
                    logger.info(
                        "deadline_job: auto-advanced kukai %d (selecting_close)", kukai_id
                    )
                    await _notify_channel(
                        _bot, kukai,
                        "選句期間が終了しました。次のステップに進んでください。",
                    )
                else:
                    await _notify_admins(
                        _bot, kukai,
                        "選句締切になりましたが、未選句の参加者がいます。"
                        f"\n句会「{kukai.title}」(ID: {kukai.id}) を確認してください。",
                    )

        except ServiceError as e:
            logger.error("deadline_job error (kukai_id=%d): %s", kukai_id, e)
        except Exception:
            logger.exception("deadline_job unexpected error (kukai_id=%d)", kukai_id)


# ── Helpers ───────────────────────────────────────────────────────────────

async def _all_submitted(session, kukai) -> bool:
    """Return True if all approved entrants have >= submission_min submissions."""
    from sqlalchemy import select
    from bot.models.entry import Entry
    from bot.repositories import submission_repo

    if not kukai.entry_enabled:
        return False

    result = await session.execute(
        select(Entry).where(
            Entry.kukai_id == kukai.id,
            Entry.status == "approved",
        )
    )
    entries = list(result.scalars().all())
    if not entries:
        return False

    for entry in entries:
        count = await submission_repo.count_user_submissions(session, kukai.id, entry.user_id)
        if count < kukai.submission_min:
            return False
    return True


async def _all_selected(session, kukai) -> bool:
    """Return True if all approved entrants have cast at least one selection."""
    from sqlalchemy import select
    from bot.models.entry import Entry
    from bot.repositories import select_repo

    if not kukai.entry_enabled:
        return False

    result = await session.execute(
        select(Entry).where(
            Entry.kukai_id == kukai.id,
            Entry.status == "approved",
        )
    )
    entries = list(result.scalars().all())
    if not entries:
        return False

    for entry in entries:
        selects = await select_repo.get_selects_by_selector(session, kukai.id, entry.user_id)
        if not selects:
            return False
    return True


async def _notify_channel(bot, kukai, message: str) -> None:
    if not kukai.channel_id:
        return
    import discord
    from bot.utils.discord_retry import send_with_retry
    from bot.utils.embed_builder import COLOR_INFO

    guild = bot.get_guild(kukai.guild_id)
    if not guild:
        return
    channel = guild.get_channel(kukai.channel_id)
    if channel and hasattr(channel, "send"):
        try:
            embed = discord.Embed(description=message, color=COLOR_INFO)
            await send_with_retry(lambda: channel.send(embed=embed))
        except Exception as e:
            logger.error("_notify_channel failed: %s", e)


async def _notify_admins(bot, kukai, message: str) -> None:
    """DM the kukai creator (and admins if reachable) with a message."""
    import discord
    from bot.utils.discord_retry import send_with_retry

    guild = bot.get_guild(kukai.guild_id)
    if not guild:
        return

    member = guild.get_member(kukai.created_by)
    if member:
        try:
            await send_with_retry(lambda: member.send(message))
        except Exception as e:
            logger.warning("_notify_admins DM failed for %d: %s", kukai.created_by, e)
            if kukai.channel_id:
                channel = guild.get_channel(kukai.channel_id)
                if channel and hasattr(channel, "send"):
                    try:
                        await send_with_retry(
                            lambda: channel.send(
                                f"<@{kukai.created_by}> {message}\n"
                                "（DM送信に失敗したためチャンネル通知に切り替えました）"
                            )
                        )
                    except Exception as channel_error:
                        logger.error("_notify_admins channel fallback failed: %s", channel_error)


async def _auto_publish_submission_list(session, kukai) -> None:
    """Publish numbered submissions to channel and store the first message ID."""
    import discord

    from bot.services import kukai_service, submission_service
    from bot.state_machine.states import KukaiState
    from bot.utils.discord_retry import send_with_retry
    from bot.utils.submission_publish import build_submission_publish_embeds

    if KukaiState.from_value(kukai.state) != KukaiState.SUBMISSION_CLOSED:
        return

    await kukai_service.jump(session, kukai, KukaiState.WAITING_PUBLISH)
    published = await submission_service.publish(session, kukai)

    if not kukai.channel_id:
        logger.warning(
            "_auto_publish_submission_list: no channel set (kukai_id=%d)",
            kukai.id,
        )
        return

    guild = _bot.get_guild(kukai.guild_id) if _bot else None
    if not guild:
        logger.warning(
            "_auto_publish_submission_list: guild not found (kukai_id=%d, guild_id=%d)",
            kukai.id,
            kukai.guild_id,
        )
        return

    channel = guild.get_channel(kukai.channel_id)
    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "_auto_publish_submission_list: text channel not found (kukai_id=%d, channel_id=%d)",
            kukai.id,
            kukai.channel_id,
        )
        return

    first_message_id: int | None = None
    embeds = build_submission_publish_embeds(kukai, published)
    try:
        for index, embed in enumerate(embeds):
            sent = await send_with_retry(lambda e=embed: channel.send(embed=e))
            if index == 0:
                first_message_id = sent.id
            if index < len(embeds) - 1:
                await asyncio.sleep(0.35)
    except Exception as error:
        logger.warning(
            "_auto_publish_submission_list: failed to post list (kukai_id=%d): %s",
            kukai.id,
            error,
        )
        return

    if first_message_id is not None:
        kukai.submission_message_id = first_message_id
