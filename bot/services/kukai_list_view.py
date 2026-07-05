"""Kukai list presentation shared by slash commands and GUI entry points."""

from __future__ import annotations

import discord

from bot.utils.datetime_utils import format_jst
from bot.utils.embed_builder import COLOR_INFO

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


def build_kukai_list_embed(kukais: list) -> discord.Embed:
    if not kukais:
        return discord.Embed(
            description="現在、開催中または招集中の句会はありません。",
            color=COLOR_INFO,
        )

    embed = discord.Embed(title="句会一覧", color=COLOR_INFO)
    for kukai in kukais[:10]:
        state_ja = STATE_LABEL.get(kukai.state, kukai.state)
        lines = [f"状態: {state_ja}"]
        if getattr(kukai, "channel_id", None):
            lines.append(f"チャンネル: <#{kukai.channel_id}>")
        if kukai.submission_close_at:
            lines.append(f"投句締切: {format_jst(kukai.submission_close_at)}")
        if kukai.selecting_close_at:
            lines.append(f"選句締切: {format_jst(kukai.selecting_close_at)}")
        embed.add_field(
            name=f"[{kukai.id}] {kukai.title}",
            value="\n".join(lines),
            inline=False,
        )
    if len(kukais) > 10:
        embed.set_footer(text=f"他 {len(kukais) - 10} 件")
    return embed
