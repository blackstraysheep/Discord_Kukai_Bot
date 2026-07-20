"""Markdown exporter for participation records."""

from __future__ import annotations

from collections import defaultdict

from bot.services.participation_record_service import ParticipationRecord, ParticipationRecordResult


def build_participation_record_markdown(
    result: ParticipationRecordResult,
    *,
    guild_names: dict[int, str],
) -> str:
    lines = [
        f"# 参加記録 - {result.target_display_name}",
        "",
        f"- 範囲: {'全サーバ' if result.scope == 'all' else 'このサーバ'}",
        f"- 表示軸: {result.group_by}",
        f"- 参加句会: {result.total_kukai_count}",
        f"- 投句: {result.submission_count}",
        f"- 選句: {result.selection_count}",
        f"- 総評: {result.overall_comment_count}",
        "",
    ]
    if not result.records:
        lines.append("該当する参加記録はありません。")
        return "\n".join(lines)

    for heading, records in _group_records(result, guild_names=guild_names).items():
        if heading:
            lines.extend([f"## {heading}", ""])
        for record in records:
            lines.extend(_record_lines(record, guild_names=guild_names))
    return "\n".join(lines).rstrip() + "\n"


def _group_records(
    result: ParticipationRecordResult,
    *,
    guild_names: dict[int, str],
) -> dict[str, list[ParticipationRecord]]:
    if result.group_by == "server":
        groups: dict[str, list[ParticipationRecord]] = defaultdict(list)
        for record in result.records:
            groups[guild_names.get(record.guild_id, f"Guild:{record.guild_id}")].append(record)
        return dict(groups)
    if result.group_by == "haigo":
        groups = defaultdict(list)
        for record in result.records:
            groups[record.participant_haigo or "俳号未設定"].append(record)
        return dict(groups)
    return {"": result.records}


def _record_lines(record: ParticipationRecord, *, guild_names: dict[int, str]) -> list[str]:
    title = f"[{record.title}]({record.title_url})" if record.title_url else record.title
    guild_name = guild_names.get(record.guild_id, f"Guild:{record.guild_id}")
    lines = [
        f"### {title}",
        "",
        f"- 俳号: {record.participant_haigo or '俳号未設定'}",
        f"- サーバ: {guild_name}",
        f"- 状態: {record.state}",
        "",
    ]
    if record.submissions:
        lines.extend(["#### 投句", ""])
        for submission in record.submissions:
            score = "" if submission.total_score is None else f"（{submission.total_score}点）"
            lines.append(f"- {submission.text}{score}")
        lines.append("")
    if record.selections_by_label:
        lines.extend(["#### 選句", ""])
        for group in record.selections_by_label:
            lines.append(f"##### {group.label}")
            for selection in group.selections:
                author = f"（{selection.author_name}）" if selection.author_name else ""
                lines.append(f"- {selection.selected_text}{author}")
                if selection.comment:
                    lines.append(f"  - 選評: {selection.comment}")
            lines.append("")
    if record.overall_comment:
        lines.extend(["#### 総評", "", record.overall_comment, ""])
    return lines
