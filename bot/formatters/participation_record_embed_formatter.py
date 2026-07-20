"""Discord embed formatter for participation records."""

from __future__ import annotations

import discord

from bot.services.participation_record_service import ParticipationRecordResult
from bot.utils.embed_builder import COLOR_INFO


def build_participation_record_summary_embed(
    result: ParticipationRecordResult,
    *,
    guild_names: dict[int, str],
    limit: int,
    filename: str,
) -> discord.Embed:
    shown = result.records[:limit]
    embed = discord.Embed(
        title=f"参加記録 — {result.target_display_name}",
        color=COLOR_INFO,
    )
    scope_label = "全サーバ" if result.scope == "all" else "このサーバ"
    embed.description = (
        f"範囲: {scope_label} / 表示軸: {result.group_by}\n"
        f"詳細は添付 `{filename}` を確認してください。"
    )
    embed.add_field(name="参加句会", value=str(result.total_kukai_count), inline=True)
    embed.add_field(name="投句", value=str(result.submission_count), inline=True)
    embed.add_field(name="選句", value=str(result.selection_count), inline=True)
    if result.overall_comment_count:
        embed.add_field(name="総評", value=str(result.overall_comment_count), inline=True)

    if not shown:
        embed.add_field(name="直近", value="該当する参加記録はありません。", inline=False)
    else:
        lines = []
        for record in shown:
            guild_name = guild_names.get(record.guild_id, f"Guild:{record.guild_id}")
            title = f"[{record.title}]({record.title_url})" if record.title_url else record.title
            haigo = record.participant_haigo or "俳号未設定"
            lines.append(f"- {title} / {guild_name} / {haigo}")
        hidden = result.total_kukai_count - len(shown)
        if hidden > 0:
            lines.append(f"...他 {hidden} 件")
        embed.add_field(name="直近", value="\n".join(lines), inline=False)

    embed.set_footer(text="Discord上は要約のみ表示します。")
    return embed
