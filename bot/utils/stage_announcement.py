"""Shared public stage announcements and action-button posts."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select as sa_select

from bot.database import get_session
from bot.models.select_rule import SelectLabel
from bot.state_machine.states import KukaiState
from bot.utils.datetime_utils import format_jst
from bot.utils.discord_retry import send_with_retry
from bot.utils.embed_builder import COLOR_INFO, build_select_summary
from bot.utils.text import discord_safe

logger = logging.getLogger(__name__)


def _mode_label(mode: str | None) -> str:
    return {
        "manual": "手動",
        "semi_auto": "半自動",
        "full_auto": "全自動",
        "auto": "自動",
    }.get(str(mode), str(mode))


def _entry_mode_label(mode: str | None) -> str:
    return {"manual": "手動", "auto": "自動", "full_auto": "自動"}.get(str(mode), str(mode))


def _limited_field_value(lines: list[str], *, limit: int = 1024) -> str:
    if not lines:
        return "（なし）"
    value = ""
    shown = 0
    for line in lines:
        candidate = f"{value}\n{line}" if value else line
        if len(candidate) > limit:
            remaining = len(lines) - shown
            suffix = f"\n...他 {remaining} 件"
            if value and len(value) + len(suffix) <= limit:
                value += suffix
            break
        value = candidate
        shown += 1
    return value or "（表示できる項目がありません）"


async def _approved_entry_lines(*, guild: discord.Guild, kukai_id: int) -> list[str]:
    from bot.models.entry import Entry

    async with get_session() as session:
        result = await session.execute(
            sa_select(Entry)
            .where(Entry.kukai_id == kukai_id, Entry.status == "approved")
            .order_by(Entry.created_at)
        )
        entries = list(result.scalars().all())

    lines: list[str] = []
    for entry in entries:
        member = guild.get_member(entry.user_id)
        name = entry.haigo or (member.display_name if member else f"UID:{entry.user_id}")
        lines.append(discord_safe(name))
    return lines


def _state_stage_label(state: KukaiState) -> str | None:
    return {
        KukaiState.ENTRY_OPEN: "エントリー受付",
        KukaiState.SUBMISSION_OPEN: "投句受付",
        KukaiState.SELECTING_OPEN: "選句受付",
        KukaiState.RESULTS: "結果公開",
    }.get(state)


def _state_announcement_description(kukai, state: KukaiState) -> str | None:
    stage = _state_stage_label(state)
    if stage:
        return f"句会「**{kukai.title}**」の **{stage}** を開始しました。"
    message = {
        KukaiState.ENTRY_CLOSED: "エントリーが締め切られました。",
        KukaiState.SUBMISSION_CLOSED: "投句が締め切られました。",
        KukaiState.WAITING_PUBLISH: "投句公開待ちになりました。",
        KukaiState.SELECTING_CLOSED: "選句が締め切られました。",
        KukaiState.ENDED: "句会が終了しました。",
        KukaiState.PAUSED: "句会が一時停止されました。",
        KukaiState.CANCELLED: "句会が中止されました。",
    }.get(state)
    if message is None:
        return None
    return f"句会「**{kukai.title}**」: {message}"


async def build_stage_announcement_embed(kukai, state: KukaiState, guild: discord.Guild) -> discord.Embed | None:
    description = _state_announcement_description(kukai, state)
    if not description:
        return None

    embed = discord.Embed(description=description, color=COLOR_INFO)
    if state == KukaiState.ENTRY_OPEN and kukai.entry_enabled and kukai.entry_close_at:
        embed.add_field(
            name=f"エントリー締切（{_entry_mode_label(getattr(kukai, 'entry_mode', 'manual'))}）",
            value=format_jst(kukai.entry_close_at),
            inline=False,
        )
    elif state == KukaiState.ENTRY_CLOSED:
        participant_lines = await _approved_entry_lines(guild=guild, kukai_id=kukai.id)
        embed.add_field(
            name=f"エントリー人数: {len(participant_lines)}名",
            value=_limited_field_value(participant_lines),
            inline=False,
        )
    elif state == KukaiState.SUBMISSION_OPEN and kukai.submission_close_at:
        embed.add_field(
            name=f"投句締切（{_mode_label(kukai.submission_mode)}）",
            value=format_jst(kukai.submission_close_at),
            inline=False,
        )
    elif state == KukaiState.SELECTING_OPEN:
        select_labels = []
        try:
            async with get_session() as session:
                result = await session.execute(
                    sa_select(SelectLabel)
                    .where(SelectLabel.kukai_id == kukai.id)
                    .order_by(SelectLabel.display_order)
                )
                select_labels = list(result.scalars().all())
        except Exception:
            logger.exception("Failed to load select labels for stage announcement")
        if select_labels:
            embed.add_field(
                name="句数",
                value=build_select_summary(kukai.submission_min, kukai.submission_max, select_labels),
                inline=False,
            )
        if kukai.selecting_close_at:
            embed.add_field(
                name=f"選句締切（{_mode_label(kukai.selecting_mode)}）",
                value=format_jst(kukai.selecting_close_at),
                inline=False,
            )
    embed.set_footer(text=f"句会ID: {kukai.id}")
    return embed


async def send_stage_announcement(guild: discord.Guild, kukai, state: KukaiState) -> bool:
    if not kukai.channel_id:
        return False
    channel = guild.get_channel(kukai.channel_id)
    if not channel or not hasattr(channel, "send"):
        return False
    embed = await build_stage_announcement_embed(kukai, state, guild)
    if embed is None:
        return False

    from bot.cogs.kukai_cog import StageActionView

    view = StageActionView(kukai.id, state)
    try:
        if view.children:
            await send_with_retry(lambda: channel.send(embed=embed, view=view))
        else:
            await send_with_retry(lambda: channel.send(embed=embed))
    except Exception:
        logger.exception("Failed to send stage announcement")
        return False
    return True


def current_action_kind(kukai) -> str | None:
    state = KukaiState.from_value(kukai.state)
    return {
        KukaiState.ENTRY_OPEN: "entry",
        KukaiState.SUBMISSION_OPEN: "submission",
        KukaiState.SELECTING_OPEN: "selecting",
        KukaiState.RESULTS: "result",
        KukaiState.ENDED: "result",
    }.get(state)


def _stage_for_kind(kind: str) -> KukaiState | None:
    return {
        "entry": KukaiState.ENTRY_OPEN,
        "submission": KukaiState.SUBMISSION_OPEN,
        "selecting": KukaiState.SELECTING_OPEN,
    }.get(kind)


async def build_action_button_message(kukai, kind: str, *, result_count: int | None = None):
    if kind == "current":
        resolved = current_action_kind(kukai)
        if resolved is None:
            return None, None, "現在の状態では投稿できる操作ボタンがありません。"
        kind = resolved

    stage = _stage_for_kind(kind)
    if stage is not None:
        from bot.cogs.kukai_cog import StageActionView

        label = {
            "entry": "エントリー",
            "submission": "投句",
            "selecting": "選句",
        }[kind]
        embed = discord.Embed(
            description=f"句会「**{kukai.title}**」の **{label}** 操作ボタンです。",
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"句会ID: {kukai.id}")
        return embed, StageActionView(kukai.id, stage), None

    if kind == "result":
        from bot.cogs.result_cog import ResultOpenView, _resolve_initial_format, build_result_entry_embed

        count = result_count if result_count is not None else 0
        return (
            build_result_entry_embed(kukai, result_count=count),
            ResultOpenView(kukai.id, initial_format=_resolve_initial_format(kukai, None)),
            None,
        )

    return None, None, "button は entry/submission/selecting/result/current で指定してください。"


async def send_action_button_message(channel, kukai, kind: str, *, result_count: int | None = None) -> str | None:
    embed, view, error = await build_action_button_message(kukai, kind, result_count=result_count)
    if error:
        return error
    if embed is None or view is None:
        return "投稿するボタンを作成できませんでした。"
    await send_with_retry(lambda: channel.send(embed=embed, view=view))
    return None
