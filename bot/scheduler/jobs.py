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
            "submission_open": "投句開始",
            "submission_close": "投句締切",
            "selecting_close": "選句締切",
            "entry_close": "エントリー締切",
            "voice_start": "ボイス句会開始",
        }
        event_ja = EVENT_JA.get(ns.event_type, ns.event_type)
        event_label = _event_label_with_mode(kukai, ns.event_type, event_ja)
        voice_session = None
        if ns.event_type == "voice_start":
            from bot.models.voice_session import VoiceSession

            voice_session = (
                await session.execute(
                    select(VoiceSession).where(VoiceSession.kukai_id == kukai.id)
                )
            ).scalar_one_or_none()
        deadline_map = {
            "submission_open": kukai.submission_open_at,
            "submission_close": kukai.submission_close_at,
            "selecting_close": kukai.selecting_close_at,
            "entry_close": kukai.entry_close_at if hasattr(kukai, "entry_close_at") else None,
            "voice_start": voice_session.start_at if voice_session else None,
        }
        deadline_dt = deadline_map.get(ns.event_type)
        hours_left = round(ns.offset_secs / 3600)
        time_str = format_jst(deadline_dt) if deadline_dt else "未定"

        embed_desc = (
            f"⏰ 「**{kukai.title}**」の **{event_label}** まで約 **{hours_left}時間** です。\n"
            f"締切: {time_str}"
        )
        if voice_session is not None:
            embed_desc += f"\n場所: <#{voice_session.vc_channel_id}>"

        import discord
        embed = discord.Embed(description=embed_desc, color=COLOR_INFO)
        embed.set_footer(text=f"句会 ID: {kukai.id}")
        guild = _bot.get_guild(kukai.guild_id)
        if ns.event_type == "entry_close" and getattr(kukai, "entry_enabled", False):
            participant_lines = await _entry_close_participant_lines(session, guild, kukai.id)
            embed.add_field(
                name="参加者一覧",
                value=_limited_field_value(participant_lines),
                inline=False,
            )

        target_user_ids = await _notification_target_user_ids(session, kukai, ns.event_type, ns.target)
        mention_text = " ".join(f"<@{user_id}>" for user_id in target_user_ids) if ns.mention else ""
        sent_count = 0
        error_msg = None

        if ns.channel_id == -2:
            from bot.services import admin_notice_service, progress_service

            fields = []
            if ns.target == "incomplete" and ns.event_type == "submission_close":
                report = await progress_service.submission_report(session, kukai)
                fields.append(("未達状況", "\n".join(report.admin_lines())))
            elif ns.target == "incomplete" and ns.event_type == "selecting_close":
                report = await progress_service.selecting_report(session, kukai)
                fields.append(("未達状況", "\n".join(report.admin_lines())))
            sent = await admin_notice_service.send_admin_notice(
                _bot,
                session,
                kukai,
                title=f"{event_label}前の管理者通知",
                description=embed_desc,
                fields=fields,
                mention_admins=ns.mention,
            )
            sent_count = 1 if sent else 0
        elif ns.channel_id == -1:
            if guild:
                for user_id in target_user_ids:
                    member = guild.get_member(user_id)
                    if not member:
                        continue
                    try:
                        await send_with_retry(lambda m=member: m.send(embed=embed))
                        sent_count += 1
                    except Exception as e:
                        error_msg = str(e)
                        logger.error("notification_job DM failed: %s", e)
        else:
            channel_id = ns.channel_id if ns.channel_id else kukai.channel_id
            if channel_id and channel_id > 0:
                if guild:
                    channel = guild.get_channel(channel_id)
                    if channel and hasattr(channel, "send"):
                        try:
                            if mention_text:
                                await send_with_retry(lambda: channel.send(content=mention_text, embed=embed))
                            else:
                                await send_with_retry(lambda: channel.send(embed=embed))
                            sent_count = max(1, len(target_user_ids) if ns.mention else 1)
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
    from bot.services import admin_notice_service, kukai_service, notification_service, progress_service
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

            if event_type == "submission_open":
                if state not in {
                    KukaiState.DRAFT,
                    KukaiState.ENTRY_OPEN,
                    KukaiState.ENTRY_CLOSED,
                }:
                    return
                await _open_submission(
                    bot=_bot,
                    session=session,
                    kukai=kukai,
                    notification_service=notification_service,
                    kukai_service=kukai_service,
                )
                return

            if event_type == "submission_close":
                mode = kukai.submission_mode
                if mode == "manual":
                    return
                if state != KukaiState.SUBMISSION_OPEN:
                    return

                report = await progress_service.submission_report(session, kukai)
                should_advance = False
                if mode == "full_auto":
                    should_advance = True
                elif mode == "semi_auto":
                    should_advance = report.complete

                if should_advance:
                    await _mark_past_deadline_notifications_fired(
                        session, kukai.id, "submission_close"
                    )
                    if mode == "full_auto" and not report.complete:
                        await admin_notice_service.send_admin_notice(
                            _bot,
                            session,
                            kukai,
                            title="投句未達警告",
                            description="全自動設定のため、投句条件未達の参加者がいても締切処理を続行します。",
                            fields=[("未達状況", "\n".join(report.admin_lines()))],
                            mention_admins=True,
                        )
                    await kukai_service.proceed(session, kukai)
                    logger.info(
                        "deadline_job: auto-advanced kukai %d to submission_closed", kukai_id
                    )
                    try:
                        await _auto_publish_submission_list(session, kukai)
                    except ServiceError as e:
                        kukai.state = KukaiState.SUBMISSION_CLOSED
                        await notification_service.schedule_kukai_jobs(session, kukai)
                        logger.error("deadline_job publish skipped (kukai_id=%d): %s", kukai_id, e)
                        await _notify_channel(
                            _bot, kukai,
                            "投句期間が終了しましたが、公開対象の投句がありません。",
                        )
                        return
                    else:
                        await kukai_service.proceed(session, kukai)
                        await notification_service.schedule_kukai_jobs(session, kukai)
                        logger.info(
                            "deadline_job: auto-published and advanced kukai %d to SELECTING_OPEN",
                            kukai_id,
                        )
                        await _notify_channel(
                            _bot, kukai,
                            "投句期間が終了したため、投句一覧を番号付きで公開して選句を開始しました。",
                        )
                        if mode == "full_auto" and not report.complete:
                            await admin_notice_service.send_admin_notice(
                                _bot,
                                session,
                                kukai,
                                title="投句未達のまま進行しました",
                                description="全自動設定により、投句条件未達の参加者がいる状態で選句受付へ進行しました。",
                                fields=[("未達状況", "\n".join(report.admin_lines()))],
                            )
                else:
                    await admin_notice_service.send_admin_notice(
                        _bot,
                        session,
                        kukai,
                        title="投句条件未達のため自動進行を停止しました",
                        description="半自動設定の締切時点で、投句条件を満たしていない参加者がいます。手動で確認してください。",
                        fields=[("未達状況", "\n".join(report.admin_lines()))],
                        mention_admins=True,
                    )
                    await _notify_channel(
                        _bot,
                        kukai,
                        "投句条件を満たしていない参加者がいるため、自動進行しませんでした。管理者確認後に進行します。",
                    )

            if event_type == "entry_close":
                if not getattr(kukai, "entry_enabled", False):
                    return
                mode = getattr(kukai, "entry_mode", "manual")
                if not kukai_service.is_entry_mode_auto(mode):
                    await _notify_entry_closed(bot=_bot, session=session, kukai=kukai)
                    return
                if state not in {KukaiState.ENTRY_OPEN, KukaiState.SUBMISSION_OPEN}:
                    return

                approved_entries = await _approved_entries(session, kukai.id)
                if not approved_entries:
                    await kukai_service.cancel(session, kukai)
                    await notification_service.cancel_kukai_jobs(session, kukai.id)
                    await admin_notice_service.send_admin_notice(
                        _bot,
                        session,
                        kukai,
                        title="エントリー人数不足のため句会不成立",
                        description=(
                            "エントリー締切（自動）時点で承認済みエントリーが0名のため、"
                            "句会を中止しました。"
                        ),
                        mention_admins=True,
                    )
                    await _notify_channel(
                        _bot,
                        kukai,
                        "エントリー締切時点で承認済みエントリーが0名のため、句会は不成立となりました。",
                    )
                    return

                await _mark_past_deadline_notifications_fired(
                    session, kukai.id, "entry_close"
                )
                await _notify_entry_closed(bot=_bot, session=session, kukai=kukai)
                if state == KukaiState.ENTRY_OPEN:
                    if getattr(kukai, "submission_open_at", None) is None:
                        await _open_submission(
                            bot=_bot,
                            session=session,
                            kukai=kukai,
                            notification_service=notification_service,
                            kukai_service=kukai_service,
                        )
                        return
                    kukai.state = KukaiState.ENTRY_CLOSED
                    kukai.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await notification_service.schedule_kukai_jobs(session, kukai)
                return

            if event_type == "selecting_close":
                mode = kukai.selecting_mode
                if mode == "manual":
                    return
                if state != KukaiState.SELECTING_OPEN:
                    await admin_notice_service.send_admin_notice(
                        _bot,
                        session,
                        kukai,
                        title="選句締切の自動処理を実行できませんでした",
                        description=(
                            f"選句締切の自動処理時刻になりましたが、現在状態は `{state.value}` です。"
                            "前段階が手動確認待ちで止まっている可能性があります。"
                        ),
                        mention_admins=True,
                    )
                    return

                report = await progress_service.selecting_report(session, kukai)
                should_advance = False
                if mode == "full_auto":
                    should_advance = True
                elif mode == "semi_auto":
                    should_advance = report.complete

                if should_advance:
                    if mode == "full_auto" and not report.complete:
                        await admin_notice_service.send_admin_notice(
                            _bot,
                            session,
                            kukai,
                            title="選句未達警告",
                            description="全自動設定のため、選句条件未達の参加者がいても締切処理を続行します。",
                            fields=[("未達状況", "\n".join(report.admin_lines()))],
                            mention_admins=True,
                        )
                    await kukai_service.proceed(session, kukai)
                    await kukai_service.proceed(session, kukai)
                    await notification_service.schedule_kukai_jobs(session, kukai)
                    logger.info(
                        "deadline_job: auto-advanced kukai %d to RESULTS (selecting_close)", kukai_id
                    )
                    result_count, result_warning = await _auto_publish_result_list(session, kukai)
                    message = "選句期間が終了したため、結果を公開しました。"
                    if result_count is not None:
                        message += f"\n公開結果数: {result_count}句"
                    if result_warning:
                        message += f"\n⚠️ {result_warning}"
                    await _notify_channel(
                        _bot, kukai,
                        message,
                    )
                    if mode == "full_auto" and not report.complete:
                        await admin_notice_service.send_admin_notice(
                            _bot,
                            session,
                            kukai,
                            title="選句未達のまま進行しました",
                            description="全自動設定により、選句条件未達の参加者がいる状態で結果公開へ進行しました。",
                            fields=[("未達状況", "\n".join(report.admin_lines()))],
                        )
                else:
                    await admin_notice_service.send_admin_notice(
                        _bot,
                        session,
                        kukai,
                        title="選句条件未達のため自動進行を停止しました",
                        description="半自動設定の締切時点で、選句条件を満たしていない参加者がいます。手動で確認してください。",
                        fields=[("未達状況", "\n".join(report.admin_lines()))],
                        mention_admins=True,
                    )
                    await _notify_channel(
                        _bot,
                        kukai,
                        "選句条件を満たしていない参加者がいるため、自動進行しませんでした。管理者確認後に進行します。",
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


async def _open_submission(*, bot, session, kukai, notification_service, kukai_service) -> None:
    from bot.state_machine.states import KukaiState

    await _mark_past_deadline_notifications_fired(session, kukai.id, "submission_open")
    await kukai_service.proceed(session, kukai)
    if KukaiState.from_value(kukai.state) != KukaiState.SUBMISSION_OPEN:
        logger.warning(
            "_open_submission did not reach submission_open (kukai_id=%s state=%s)",
            kukai.id,
            kukai.state,
        )
        return
    await notification_service.schedule_kukai_jobs(session, kukai)
    await _notify_submission_open(bot=bot, kukai=kukai)


async def _notify_submission_open(*, bot, kukai) -> None:
    if not kukai.channel_id:
        return
    import discord

    from bot.cogs.kukai_cog import StageActionView
    from bot.state_machine.states import KukaiState
    from bot.utils.datetime_utils import format_jst
    from bot.utils.discord_retry import send_with_retry
    from bot.utils.embed_builder import COLOR_INFO

    guild = bot.get_guild(kukai.guild_id) if bot else None
    if not guild:
        return
    channel = guild.get_channel(kukai.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    embed = discord.Embed(
        description=f"句会「**{kukai.title}**」の **投句受付** を開始しました。",
        color=COLOR_INFO,
    )
    if kukai.submission_close_at:
        embed.add_field(
            name=f"投句締切（{_mode_label(kukai.submission_mode)}）",
            value=format_jst(kukai.submission_close_at),
            inline=False,
        )
    embed.set_footer(text=f"句会ID: {kukai.id}")
    view = StageActionView(kukai.id, KukaiState.SUBMISSION_OPEN)
    try:
        await send_with_retry(lambda: channel.send(embed=embed, view=view))
    except Exception as error:
        logger.error("_notify_submission_open failed: %s", error)


def _mode_label(mode: str | None) -> str:
    return {
        "manual": "手動",
        "semi_auto": "半自動",
        "full_auto": "全自動",
        "auto": "自動",
    }.get(str(mode), str(mode))


def _entry_mode_label(mode: str | None) -> str:
    return {"manual": "手動", "auto": "自動", "full_auto": "自動"}.get(str(mode), str(mode))


def _event_label_with_mode(kukai, event_type: str, fallback: str) -> str:
    if event_type == "entry_close":
        return f"{fallback}（{_entry_mode_label(getattr(kukai, 'entry_mode', 'manual'))}）"
    if event_type == "submission_close":
        return f"{fallback}（{_mode_label(getattr(kukai, 'submission_mode', 'manual'))}）"
    if event_type == "selecting_close":
        return f"{fallback}（{_mode_label(getattr(kukai, 'selecting_mode', 'manual'))}）"
    return fallback


async def _approved_entries(session, kukai_id: int):
    from sqlalchemy import select

    from bot.models.entry import Entry

    result = await session.execute(
        select(Entry)
        .where(Entry.kukai_id == kukai_id, Entry.status == "approved")
        .order_by(Entry.created_at)
    )
    return list(result.scalars().all())


async def _notify_entry_closed(*, bot, session, kukai) -> None:
    import discord

    from bot.utils.discord_retry import send_with_retry
    from bot.utils.embed_builder import COLOR_INFO
    from bot.utils.text import discord_safe

    if not kukai.channel_id:
        return
    guild = bot.get_guild(kukai.guild_id) if bot else None
    if not guild:
        return
    channel = guild.get_channel(kukai.channel_id)
    if not channel or not hasattr(channel, "send"):
        return

    entries = await _approved_entries(session, kukai.id)
    names: list[str] = []
    for entry in entries:
        member = guild.get_member(entry.user_id)
        names.append(discord_safe(entry.haigo or (member.display_name if member else f"UID:{entry.user_id}")))

    embed = discord.Embed(
        description=f"句会「**{kukai.title}**」: エントリーが締め切られました。",
        color=COLOR_INFO,
    )
    embed.add_field(
        name=f"エントリー人数: {len(names)}名",
        value=_limited_field_value(names),
        inline=False,
    )
    embed.set_footer(text=f"句会ID: {kukai.id}")
    try:
        await send_with_retry(lambda: channel.send(embed=embed))
    except Exception as error:
        logger.error("_notify_entry_closed failed: %s", error)


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


async def _mark_past_deadline_notifications_fired(session, kukai_id: int, event_type: str) -> None:
    """Suppress reminder jobs for a deadline already handled by auto progression."""
    from sqlalchemy import select
    from apscheduler.jobstores.base import JobLookupError

    from bot.models.notification import NotificationSchedule
    from bot.scheduler.setup import get_scheduler, has_scheduler

    result = await session.execute(
        select(NotificationSchedule).where(
            NotificationSchedule.kukai_id == kukai_id,
            NotificationSchedule.event_type == event_type,
            NotificationSchedule.fired == False,
        )
    )
    schedules = list(result.scalars().all())
    scheduler = get_scheduler() if has_scheduler() else None
    for schedule in schedules:
        if scheduler is not None and schedule.job_id:
            try:
                scheduler.remove_job(schedule.job_id)
            except JobLookupError:
                pass
        schedule.job_id = None
        schedule.fired = True


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


async def _notification_target_user_ids(session, kukai, event_type: str, target: str) -> list[int]:
    from sqlalchemy import select
    from bot.models.entry import Entry
    from bot.models.kukai import KukaiAdmin
    from bot.repositories import select_repo, submission_repo

    if target == "admin":
        result = await session.execute(select(KukaiAdmin).where(KukaiAdmin.kukai_id == kukai.id))
        ids = [kukai.created_by] + [row.user_id for row in result.scalars().all()]
        return list(dict.fromkeys(ids))

    if target == "incomplete":
        if not kukai.entry_enabled:
            return []
        result = await session.execute(
            select(Entry).where(Entry.kukai_id == kukai.id, Entry.status == "approved")
        )
        entries = list(result.scalars().all())
        incomplete: list[int] = []
        if event_type in {"submission_close", "entry_close"}:
            for entry in entries:
                count = await submission_repo.count_user_submissions(kukai_id=kukai.id, session=session, user_id=entry.user_id)
                if count < kukai.submission_min:
                    incomplete.append(entry.user_id)
        elif event_type == "selecting_close":
            for entry in entries:
                selects = await select_repo.get_selects_by_selector(session, kukai.id, entry.user_id)
                if not selects:
                    incomplete.append(entry.user_id)
        return incomplete

    if kukai.entry_enabled:
        result = await session.execute(
            select(Entry).where(Entry.kukai_id == kukai.id, Entry.status == "approved")
        )
        return [entry.user_id for entry in result.scalars().all()]
    return [kukai.created_by]


def _limited_field_value(lines: list[str], *, limit: int = 1024) -> str:
    if not lines:
        return "（なし）"
    value = ""
    shown = 0
    for line in lines:
        candidate = f"{value}\n{line}" if value else line
        if len(candidate) > limit:
            remaining = len(lines) - shown
            suffix = f"\n…他 {remaining} 件"
            if value and len(value) + len(suffix) <= limit:
                value += suffix
            break
        value = candidate
        shown += 1
    return value or "（表示できる項目がありません）"


async def _entry_close_participant_lines(session, guild, kukai_id: int) -> list[str]:
    from sqlalchemy import select

    from bot.models.entry import Entry
    from bot.utils.text import discord_safe

    result = await session.execute(
        select(Entry)
        .where(
            Entry.kukai_id == kukai_id,
            Entry.status.in_(["approved", "pending"]),
        )
        .order_by(Entry.created_at)
    )
    entries = list(result.scalars().all())
    lines: list[str] = []
    for entry in entries:
        icon = "✅" if entry.status == "approved" else "⏳"
        status = "承認済" if entry.status == "approved" else "審査待ち"
        if entry.haigo:
            name = discord_safe(entry.haigo)
        else:
            member = guild.get_member(entry.user_id) if guild else None
            name = discord_safe(member.display_name if member else f"UID:{entry.user_id}")
        lines.append(f"{icon} {name}（{status}）")
    return lines


async def _auto_publish_result_list(session, kukai) -> tuple[int | None, str | None]:
    """Publish result embeds to channel and store the first message ID."""
    import discord

    from bot.services import result_service
    from bot.state_machine.states import KukaiState
    from bot.utils.discord_retry import send_with_retry
    from bot.cogs.result_cog import ResultOpenView, build_result_entry_embed, _resolve_initial_format

    if KukaiState.from_value(kukai.state) != KukaiState.RESULTS:
        return None, "句会状態が結果公開中ではないため、結果投稿をスキップしました。"

    results = await result_service.compute_results(session, kukai)
    if not results:
        return 0, "集計対象の投句がありません。"
    if not kukai.channel_id:
        logger.warning(
            "_auto_publish_result_list: no channel set (kukai_id=%d)",
            kukai.id,
        )
        return len(results), "公開先チャンネルが未設定です。"

    guild = _bot.get_guild(kukai.guild_id) if _bot else None
    if not guild:
        logger.warning(
            "_auto_publish_result_list: guild not found (kukai_id=%d, guild_id=%d)",
            kukai.id,
            kukai.guild_id,
        )
        return len(results), "サーバーが見つかりません。"

    channel = guild.get_channel(kukai.channel_id)
    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "_auto_publish_result_list: text channel not found (kukai_id=%d, channel_id=%d)",
            kukai.id,
            kukai.channel_id,
        )
        return len(results), "公開先テキストチャンネルが見つかりません。"

    first_message_id: int | None = None
    try:
        initial_format = _resolve_initial_format(kukai, None)
        sent = await send_with_retry(
            lambda: channel.send(
                embed=build_result_entry_embed(kukai, result_count=len(results)),
                view=ResultOpenView(kukai.id, initial_format=initial_format),
            )
        )
        first_message_id = sent.id
    except Exception as error:
        logger.warning(
            "_auto_publish_result_list: failed to post result (kukai_id=%d): %s",
            kukai.id,
            error,
        )
        return len(results), "結果メッセージ送信に失敗しました。"

    if first_message_id is not None:
        kukai.result_message_id = first_message_id
    return len(results), None
