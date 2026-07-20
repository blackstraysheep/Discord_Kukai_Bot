"""Discord embed formatter for participation records."""

from __future__ import annotations

import discord

from bot.services.participation_record_service import ParticipationRecord, ParticipationRecordResult
from bot.utils.embed_builder import COLOR_INFO


def build_participation_record_summary_embed(
    result: ParticipationRecordResult,
    *,
    guild_names: dict[int, str],
    limit: int | None,
    filename: str,
) -> discord.Embed:
    requested = result.records if limit is None else result.records[:limit]
    embed = discord.Embed(
        title=f"参加記録 — {result.target_display_name}",
        color=COLOR_INFO,
    )
    scope_label = "全サーバ" if result.scope == "all" else "このサーバ"
    embed.description = (
        f"範囲: {scope_label} / 表示軸: {result.group_by}\n"
        f"Embedに収まらない詳細、選評、総評は添付 `{filename}` を確認してください。"
    )
    embed.add_field(name="参加句会", value=str(result.total_kukai_count), inline=True)
    embed.add_field(name="投句", value=str(result.submission_count), inline=True)
    embed.add_field(name="選句", value=str(result.selection_count), inline=True)
    if result.overall_comment_count:
        embed.add_field(name="総評", value=str(result.overall_comment_count), inline=True)

    if not requested:
        embed.add_field(name="直近", value="該当する参加記録はありません。", inline=False)
        embed.set_footer(text="全内容は添付Markdownに収録しています。")
        return embed

    fully_shown = 0
    partial = False
    for record in requested:
        chunks, omitted_line = _record_chunks(record, guild_names=guild_names)
        added_chunks = 0
        for index, chunk in enumerate(chunks):
            field_name = _record_field_name(record, continuation=index > 0)
            if not _can_add_field(embed, field_name, chunk):
                partial = True
                break
            embed.add_field(name=field_name, value=chunk, inline=False)
            added_chunks += 1
        if added_chunks == len(chunks) and not omitted_line:
            fully_shown += 1
        else:
            partial = True
            break

    remaining = result.total_kukai_count - fully_shown
    target_label = "全件" if limit is None else f"最大{limit}件"
    footer = f"表示対象: {target_label} / 詳細を全表示: {fully_shown}件"
    if remaining > 0:
        footer += f" / 未収録: {remaining}件"
    if partial:
        footer += "（一部詳細を含む）"
    embed.set_footer(text=footer + "。全内容は添付Markdownを確認してください。")
    return embed


_FIELD_VALUE_LIMIT = 1024
_FIELD_COUNT_LIMIT = 25
_EMBED_TOTAL_LIMIT = 6000
_FOOTER_RESERVE = 180


def _record_field_name(record: ParticipationRecord, *, continuation: bool) -> str:
    title = f"[{record.title}]({record.title_url})" if record.title_url else record.title
    if continuation:
        title += "（続き）"
    if len(title) <= 256:
        return title
    return f"句会ID: {record.kukai_id}" + ("（続き）" if continuation else "")


def _record_chunks(
    record: ParticipationRecord,
    *,
    guild_names: dict[int, str],
) -> tuple[list[str], bool]:
    guild_name = guild_names.get(record.guild_id, f"Guild:{record.guild_id}")
    lines = [
        f"サーバー: {guild_name}",
        f"俳号: {record.participant_haigo or '俳号未設定'} / 状態: {record.state}",
        "投句:",
    ]
    if record.submissions:
        for submission in record.submissions:
            score = "" if submission.total_score is None else f"（{submission.total_score}点）"
            lines.extend(_content_lines("・", submission.text, score))
    else:
        lines.append("・なし")

    lines.append("選句:")
    if record.selections_by_label:
        for group in record.selections_by_label:
            lines.append(f"【{group.label}】")
            for selection in group.selections:
                author = f"（{selection.author_name}）" if selection.author_name else ""
                lines.extend(_content_lines("・", selection.selected_text, author))
    else:
        lines.append("・なし")
    return _chunk_lines(lines)


def _content_lines(prefix: str, content: str, suffix: str) -> list[str]:
    parts = content.splitlines() or [""]
    return [f"{prefix}{parts[0]}{suffix}", *[f"　{part}" for part in parts[1:]]]


def _chunk_lines(lines: list[str]) -> tuple[list[str], bool]:
    chunks: list[str] = []
    current: list[str] = []
    omitted = False
    for line in lines:
        if len(line) > _FIELD_VALUE_LIMIT:
            omitted = True
            continue
        candidate = "\n".join([*current, line])
        if len(candidate) <= _FIELD_VALUE_LIMIT:
            current.append(line)
            continue
        if current:
            chunks.append("\n".join(current))
        current = [line]
    if current:
        chunks.append("\n".join(current))
    return chunks, omitted


def _can_add_field(embed: discord.Embed, name: str, value: str) -> bool:
    if len(embed.fields) >= _FIELD_COUNT_LIMIT:
        return False
    return len(embed) + len(name) + len(value) + _FOOTER_RESERVE <= _EMBED_TOTAL_LIMIT
