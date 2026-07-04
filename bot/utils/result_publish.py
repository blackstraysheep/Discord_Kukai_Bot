"""Result publish embed builders for automatic channel posting."""

from __future__ import annotations

import discord

from bot.utils.embed_builder import COLOR_INFO, COLOR_RESULT
from bot.utils.submission_markup import discord_safe_submission_text
from bot.utils.text import discord_safe

COMMENT_PREVIEW_LIMIT = 300
OVERALL_PREVIEW_LIMIT = 1000

def _display_name(user_id: int, guild: discord.Guild, display_names: dict[int, str]) -> str:
    if user_id in display_names:
        return display_names[user_id]
    member = guild.get_member(user_id)
    return member.display_name if member else f"UID:{user_id}"


def _label_select_summary(label_select, guild: discord.Guild, display_names: dict[int, str]) -> str:
    selector_names = [
        discord_safe(_display_name(user_id, guild, display_names))
        for user_id in label_select.selector_user_ids
    ]
    selector_suffix = f"（{'・'.join(selector_names)}）" if selector_names else ""
    return f"{discord_safe(label_select.label)}×{label_select.count}{selector_suffix}"


def _comment_signature(user_id: int, guild: discord.Guild, display_names: dict[int, str]) -> str:
    return discord_safe(_display_name(user_id, guild, display_names))


def _score_embeds(
    kukai,
    results,
    *,
    reveal_author_for_user: dict[int, bool],
    guild: discord.Guild,
    display_names: dict[int, str],
) -> list[discord.Embed]:
    pages: list[discord.Embed] = []
    embed = discord.Embed(
        title=f"🏆 選句結果 — {kukai.title}",
        color=COLOR_RESULT,
    )
    char_count = len(embed.title)

    for result in results:
        author_line = ""
        if reveal_author_for_user.get(result.author_user_id, False):
            author_name = _display_name(result.author_user_id, guild, display_names)
            author_line = f"　作者: {discord_safe(author_name)}"

        label_parts = [
            _label_select_summary(level, guild, display_names)
            for level in result.label_selects
        ]
        label_str = "　".join(label_parts) if label_parts else "（無選）"
        header = f"**{result.rank}位 ({result.total_score}点)** — No.{result.number}{author_line}"

        body_lines = [f"> {discord_safe_submission_text(result.text)}", label_str]
        for level in result.label_selects:
            for comment in level.comments[:3]:
                body_lines.append(
                    f"　💬 [{level.label}] {discord_safe(comment.text[:COMMENT_PREVIEW_LIMIT])}"
                    f"（{_comment_signature(comment.selector_user_id, guild, display_names)}）"
                )
        body = "\n".join(body_lines)

        if len(embed.fields) >= 25 or char_count + len(header) + len(body) > 5800:
            pages.append(embed)
            embed = discord.Embed(color=COLOR_RESULT)
            char_count = 0

        embed.add_field(name=header, value=body, inline=False)
        char_count += len(header) + len(body)

    embed.set_footer(text=f"句会 ID: {kukai.id}　|　全 {len(results)} 句")
    pages.append(embed)
    return pages


def _number_embeds(kukai, results, guild: discord.Guild, display_names: dict[int, str]) -> list[discord.Embed]:
    sorted_results = sorted(results, key=lambda item: item.number)
    lines = []
    for result in sorted_results:
        label = "　".join(
            _label_select_summary(level, guild, display_names)
            for level in result.label_selects
        ) or "（無選）"
        lines.append(f"`No.{result.number}` {discord_safe_submission_text(result.text)}　— {label} ({result.total_score}点)")

    embed = discord.Embed(
        title=f"📋 選句結果（番号順） — {kukai.title}",
        description="\n".join(lines[:40]),
        color=COLOR_INFO,
    )
    if len(sorted_results) > 40:
        embed.set_footer(text=f"他 {len(sorted_results) - 40} 句　|　句会 ID: {kukai.id}")
    else:
        embed.set_footer(text=f"全 {len(sorted_results)} 句　|　句会 ID: {kukai.id}")
    return [embed]


def _overall_embeds(kukai, overall_comments, guild: discord.Guild, display_names: dict[int, str]) -> list[discord.Embed]:
    if not overall_comments:
        return []

    pages: list[discord.Embed] = []
    embed = discord.Embed(title=f"📝 総評 — {kukai.title}", color=COLOR_INFO)
    char_count = len(embed.title)

    for overall in overall_comments:
        user_name = _display_name(overall.user_id, guild, display_names)
        header = discord_safe(user_name)
        body = discord_safe(overall.comment[:OVERALL_PREVIEW_LIMIT])
        if len(embed.fields) >= 25 or char_count + len(header) + len(body) > 5800:
            pages.append(embed)
            embed = discord.Embed(color=COLOR_INFO)
            char_count = 0
        embed.add_field(name=header, value=body, inline=False)
        char_count += len(header) + len(body)

    embed.set_footer(text=f"句会 ID: {kukai.id}　|　総評 {len(overall_comments)} 件")
    pages.append(embed)
    return pages


def _author_embeds(
    kukai,
    results,
    guild: discord.Guild,
    *,
    visible_author_ids: set[int],
    display_names: dict[int, str],
) -> list[discord.Embed]:
    from collections import defaultdict

    by_author: dict[int, list] = defaultdict(list)
    for result in results:
        if result.author_user_id not in visible_author_ids:
            continue
        by_author[result.author_user_id].append(result)

    if not by_author:
        return [discord.Embed(description="公開対象の作者がいないため、作者別表示はできません。", color=COLOR_INFO)]

    embed = discord.Embed(
        title=f"👤 選句結果（作者別） — {kukai.title}",
        color=COLOR_RESULT,
    )
    for user_id, subs in by_author.items():
        author_name = _display_name(user_id, guild, display_names)
        total = sum(item.total_score for item in subs)
        lines = [
            f"`No.{item.number}` {discord_safe_submission_text(item.text)} — {item.total_score}点 ({item.rank}位)"
            for item in subs
        ]
        embed.add_field(
            name=f"{discord_safe(author_name)} (合計 {total}点)",
            value="\n".join(lines),
            inline=False,
        )
        if len(embed.fields) >= 25:
            break
    embed.set_footer(text=f"句会 ID: {kukai.id}")
    return [embed]


def build_result_publish_embeds(
    kukai,
    results,
    overall_comments,
    guild: discord.Guild,
    *,
    display_names: dict[int, str] | None = None,
) -> list[discord.Embed]:
    display_names = display_names or {}
    totals: dict[int, int] = {}
    for result in results:
        totals[result.author_user_id] = totals.get(result.author_user_id, 0) + result.total_score

    if not kukai.author_reveal:
        visible_author_ids: set[int] = set()
    elif kukai.author_reveal_zero:
        visible_author_ids = set(totals.keys())
    else:
        visible_author_ids = {uid for uid, score in totals.items() if score > 0}
    reveal_map = {uid: uid in visible_author_ids for uid in totals.keys()}

    pages: list[discord.Embed] = []
    if kukai.points_enabled:
        pages.extend(
            _score_embeds(
                kukai,
                results,
                reveal_author_for_user=reveal_map,
                guild=guild,
                display_names=display_names,
            )
        )
    pages.extend(_number_embeds(kukai, results, guild, display_names))
    if kukai.author_reveal:
        pages.extend(
            _author_embeds(
                kukai,
                results,
                guild,
                visible_author_ids=visible_author_ids,
                display_names=display_names,
            )
        )
    pages.extend(_overall_embeds(kukai, overall_comments, guild, display_names))
    return pages
