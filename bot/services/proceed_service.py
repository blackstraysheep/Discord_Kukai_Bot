"""Shared kukai proceed workflow for slash commands and GUI controls."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import discord
from discord.ext import commands
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services import (
    admin_notice_service,
    kukai_service,
    notification_service,
    progress_service,
    result_service,
    submission_service,
)
from bot.state_machine.states import KukaiState
from bot.utils.discord_retry import send_with_retry
from bot.utils.stage_announcement import send_stage_announcement
from bot.utils.submission_publish import build_submission_publish_embeds

logger = logging.getLogger(__name__)


STATE_LABEL: dict[str, str] = {
    "draft": "開始前",
    "entry_open": "エントリー受付中",
    "entry_closed": "エントリー締切",
    "submission_open": "投句受付中",
    "submission_closed": "投句締切",
    "waiting_publish": "投句公開待ち",
    "selecting_open": "選句受付中",
    "selecting_closed": "選句締切",
    "waiting_results": "結果公開待ち",
    "results": "結果公開中",
    "ended": "終了",
    "paused": "一時停止",
    "cancelled": "中止",
}


@dataclass(frozen=True)
class ProceedPreview:
    current_state: KukaiState
    next_state: KukaiState
    progress_report: progress_service.ProgressReport | None
    effects: tuple[str, ...]

    @property
    def has_incomplete(self) -> bool:
        return self.progress_report is not None and not self.progress_report.complete


@dataclass(frozen=True)
class ProceedResult:
    kukai_id: int
    title: str
    before_state: KukaiState
    after_state: KukaiState
    published_count: int | None = None
    publish_warning: str | None = None
    result_count: int | None = None
    result_warning: str | None = None
    override_report: progress_service.ProgressReport | None = None

    def success_description(self) -> str:
        state_ja = STATE_LABEL.get(self.after_state.value, self.after_state.value)
        description = f"句会「{self.title}」を **{state_ja}** へ進めました。"
        if self.published_count is not None:
            description += f"\n{self.published_count}句を番号付きで公開しました。"
            if self.publish_warning:
                description += f"\n⚠️ {self.publish_warning}"
        if self.result_count is not None:
            description += f"\n結果 {self.result_count}句を公開しました。"
            if self.result_warning:
                description += f"\n⚠️ {self.result_warning}"
        return description


class ProceedNeedsConfirmation(Exception):
    def __init__(self, preview: ProceedPreview) -> None:
        self.preview = preview
        super().__init__(preview.progress_report.summary() if preview.progress_report else "確認が必要です。")


async def preview_proceed(session: AsyncSession, kukai) -> ProceedPreview:
    current_state = KukaiState.from_value(kukai.state)
    next_state = await _predict_next_state(session, kukai, current_state)
    report = await progress_service.report_for_state(session, kukai, current_state)
    return ProceedPreview(
        current_state=current_state,
        next_state=next_state,
        progress_report=report,
        effects=tuple(_effect_lines(current_state, next_state)),
    )


async def execute_proceed(
    *,
    bot: commands.Bot,
    session: AsyncSession,
    guild: discord.Guild,
    kukai,
    actor_user_id: int,
    source_label: str,
    interaction_id: int | None = None,
    channel_id: int | None = None,
    allow_incomplete: bool = False,
) -> ProceedResult:
    preview = await preview_proceed(session, kukai)
    if preview.has_incomplete and not allow_incomplete:
        raise ProceedNeedsConfirmation(preview)

    current_state = preview.current_state
    published_count: int | None = None
    publish_warning: str | None = None
    result_count: int | None = None
    result_warning: str | None = None

    logger.info(
        "event=kukai_proceed_start kukai_id=%s actor_user_id=%s source=%s "
        "before_state=%s interaction_id=%s channel_id=%s",
        kukai.id,
        actor_user_id,
        source_label,
        current_state,
        interaction_id,
        channel_id,
    )

    if current_state in {KukaiState.SUBMISSION_CLOSED, KukaiState.WAITING_PUBLISH}:
        await kukai_service.jump(session, kukai, KukaiState.WAITING_PUBLISH)
        published = await submission_service.publish(session, kukai)
        published_count = len(published)
        publish_warning, message_id = await post_submission_list(guild, kukai, published)
        if message_id is not None:
            kukai.submission_message_id = message_id
        new_state = await kukai_service.proceed(session, kukai)
    else:
        new_state = await kukai_service.proceed(session, kukai)
        if new_state == KukaiState.RESULTS:
            result_count, result_warning, result_message_id = await post_result_entry(session, guild, kukai)
            if result_message_id is not None:
                kukai.result_message_id = result_message_id

    if new_state == KukaiState.SUBMISSION_CLOSED:
        from bot.scheduler import jobs as scheduler_jobs

        await scheduler_jobs.notify_entry_closed_for_manual_submission_close(
            bot=bot,
            session=session,
            kukai=kukai,
            previous_state=current_state,
        )

    if preview.has_incomplete:
        report = preview.progress_report
        assert report is not None
        await admin_notice_service.send_admin_notice(
            bot,
            session,
            kukai,
            title="条件未達のまま手動進行しました",
            description=(
                f"<@{actor_user_id}> が {source_label} で確認し、"
                "条件未達の参加者がいる状態で句会を進行しました。"
            ),
            fields=[("未達状況", "\n".join(report.admin_lines()))],
        )

    await notification_service.cancel_kukai_jobs(session, kukai.id)
    await notification_service.schedule_kukai_jobs(session, kukai)

    logger.info(
        "event=kukai_proceed kukai_id=%s actor_user_id=%s source=%s "
        "before_state=%s after_state=%s interaction_id=%s channel_id=%s",
        kukai.id,
        actor_user_id,
        source_label,
        current_state,
        new_state,
        interaction_id,
        channel_id,
    )
    return ProceedResult(
        kukai_id=kukai.id,
        title=kukai.title,
        before_state=current_state,
        after_state=new_state,
        published_count=published_count,
        publish_warning=publish_warning,
        result_count=result_count,
        result_warning=result_warning,
        override_report=preview.progress_report if preview.has_incomplete else None,
    )


async def announce_proceed_result(guild: discord.Guild, kukai, state: KukaiState) -> None:
    await send_stage_announcement(guild, kukai, state)


async def post_submission_list(
    guild: discord.Guild,
    kukai,
    published_submissions,
) -> tuple[str | None, int | None]:
    if not kukai.channel_id:
        return "公開先チャンネルが未設定のため、投句一覧を投稿できません。", None

    channel = guild.get_channel(kukai.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return "公開先チャンネルが見つからないため、投句一覧を投稿できません。", None

    embeds = build_submission_publish_embeds(kukai, published_submissions)
    first_message_id: int | None = None
    try:
        for index, embed in enumerate(embeds):
            sent = await send_with_retry(lambda e=embed: channel.send(embed=e))
            if index == 0:
                first_message_id = sent.id
            if index < len(embeds) - 1:
                await asyncio.sleep(0.35)
    except discord.Forbidden:
        return "公開チャンネルへの送信権限がないため、投句一覧を投稿できません。", None

    return None, first_message_id


async def post_result_entry(
    session: AsyncSession,
    guild: discord.Guild,
    kukai,
) -> tuple[int | None, str | None, int | None]:
    if not kukai.channel_id:
        return None, "公開先チャンネルが未設定のため、結果を投稿できません。", None

    channel = guild.get_channel(kukai.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return None, "公開先チャンネルが見つからないため、結果を投稿できません。", None

    results = await result_service.compute_results(session, kukai)
    if not results:
        return 0, "集計対象の投句がないため、結果投稿をスキップしました。", None
    from bot.cogs.result_cog import ResultOpenView, _resolve_initial_format, build_result_entry_embed

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
    except discord.Forbidden:
        return len(results), "公開チャンネルへの送信権限がないため、結果を投稿できません。", None

    return len(results), None, first_message_id


async def _predict_next_state(
    session: AsyncSession,
    kukai,
    current_state: KukaiState,
) -> KukaiState:
    del session
    from bot.state_machine.transitions import next_state

    if current_state in {KukaiState.SUBMISSION_CLOSED, KukaiState.WAITING_PUBLISH}:
        original_state = kukai.state
        kukai.state = KukaiState.WAITING_PUBLISH.value
        try:
            return next_state(kukai)
        finally:
            kukai.state = original_state
    return next_state(kukai)


def _effect_lines(current_state: KukaiState, next_state: KukaiState) -> list[str]:
    lines = [f"状態: {state_label(current_state)} -> {state_label(next_state)}"]
    if current_state in {KukaiState.SUBMISSION_CLOSED, KukaiState.WAITING_PUBLISH}:
        lines.append("投句一覧を番号付きで投稿します。")
    if next_state == KukaiState.RESULTS:
        lines.append("結果公開ボタンを投稿します。")
    if next_state == KukaiState.SUBMISSION_CLOSED:
        lines.append("投句締切の案内を必要に応じて送信します。")
    lines.append("通知ジョブを再登録します。")
    lines.append("句会チャンネルへ進行通知を送信します。")
    return lines


def state_label(state: KukaiState) -> str:
    return STATE_LABEL.get(state.value, state.value)
