"""Shared messages posted after a kukai is created."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import discord

from bot.state_machine.states import KukaiState
from bot.utils.datetime_utils import format_jst
from bot.utils.discord_retry import send_with_retry
from bot.utils.embed_builder import COLOR_INFO, COLOR_SUCCESS, build_select_summary
from bot.utils.stage_announcement import build_stage_announcement_embed


def _mode_label(mode: str | None) -> str:
    return {
        "manual": "手動",
        "semi_auto": "半自動",
        "full_auto": "全自動",
        "auto": "自動",
    }.get(str(mode), str(mode))


def _entry_mode_label(mode: str | None) -> str:
    return {"manual": "手動", "auto": "自動", "full_auto": "自動"}.get(str(mode), str(mode))


def build_created_kukai_success_embed(
    *,
    title: str,
    channel_mention: str,
    kukai_id: int,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
) -> discord.Embed:
    description = (
        f"句会「**{title}**」を作成しました。\n"
        f"チャンネル: {channel_mention}\n"
        f"句会ID: `{kukai_id}`"
    )
    if notes:
        description += "\n\n" + "\n".join(notes)
    for warning in warnings or []:
        if warning:
            description += f"\n\n⚠️ {warning.strip()}"
    return discord.Embed(
        title="✅ 句会作成完了",
        description=description,
        color=COLOR_SUCCESS,
    )


def build_created_kukai_info_embed(
    kukai,
    *,
    select_label_specs: list[Any],
    summary_override: str | None = None,
    voice_channel_id: int | None = None,
    voice_start_at: datetime | None = None,
    voice_end_at: datetime | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 {kukai.title}",
        description=kukai.description or "",
        color=COLOR_INFO,
    )
    if kukai.theme:
        embed.add_field(name="題", value=kukai.theme, inline=True)
    embed.add_field(
        name="句数",
        value=build_select_summary(
            kukai.submission_min,
            kukai.submission_max,
            select_label_specs,
            override_text=summary_override,
        ),
        inline=False,
    )
    if kukai.entry_enabled:
        entry_deadline = format_jst(kukai.entry_close_at) if kukai.entry_close_at else "未定"
        embed.add_field(
            name=f"エントリー締切（{_entry_mode_label(getattr(kukai, 'entry_mode', 'manual'))}）",
            value=entry_deadline,
            inline=False,
        )
    if getattr(kukai, "submission_open_at", None):
        embed.add_field(name="投句開始", value=format_jst(kukai.submission_open_at), inline=False)
    submission_deadline = format_jst(kukai.submission_close_at) if kukai.submission_close_at else "未定"
    selecting_deadline = format_jst(kukai.selecting_close_at) if kukai.selecting_close_at else "未定"
    embed.add_field(
        name=f"投句締切（{_mode_label(getattr(kukai, 'submission_mode', 'manual'))}）",
        value=submission_deadline,
        inline=False,
    )
    embed.add_field(
        name=f"選句締切（{_mode_label(getattr(kukai, 'selecting_mode', 'manual'))}）",
        value=selecting_deadline,
        inline=False,
    )
    if voice_channel_id and voice_start_at:
        voice_value = f"開始: {format_jst(voice_start_at)}\n場所: <#{voice_channel_id}>"
        if voice_end_at:
            voice_value += f"\n終了: {format_jst(voice_end_at)}"
        embed.add_field(name="ボイス句会", value=voice_value, inline=False)
    embed.set_footer(text=f"句会ID: {kukai.id}")
    return embed


async def post_created_kukai_channel_messages(
    *,
    guild: discord.Guild,
    channel: discord.TextChannel,
    kukai,
    select_label_specs: list[Any],
    summary_override: str | None = None,
    voice_channel_id: int | None = None,
    voice_start_at: datetime | None = None,
    voice_end_at: datetime | None = None,
) -> None:
    await send_with_retry(
        lambda: channel.send(
            embed=build_created_kukai_info_embed(
                kukai,
                select_label_specs=select_label_specs,
                summary_override=summary_override,
                voice_channel_id=voice_channel_id,
                voice_start_at=voice_start_at,
                voice_end_at=voice_end_at,
            )
        )
    )
    if not kukai.entry_enabled:
        return

    embed = await build_stage_announcement_embed(kukai, KukaiState.ENTRY_OPEN, guild)
    if embed is None:
        return

    from bot.cogs.kukai_cog import StageActionView

    await send_with_retry(
        lambda: channel.send(embed=embed, view=StageActionView(kukai.id, KukaiState.ENTRY_OPEN))
    )
