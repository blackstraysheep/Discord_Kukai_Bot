"""Build personal kukai participation status embeds."""

from __future__ import annotations

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.select_rule import SelectLabel
from bot.models.entry import Entry
from bot.models.kukai import Kukai, KukaiAdmin
from bot.models.select import OverallSelectComment, Select
from bot.models.submission import Submission
from bot.repositories import entry_repo, select_repo, submission_repo
from bot.state_machine.states import KukaiState
from bot.utils.embed_builder import COLOR_INFO
from bot.utils.datetime_utils import format_jst
from bot.utils.submission_markup import discord_safe_submission_text
from bot.utils.text import discord_safe

EMBED_FIELD_VALUE_LIMIT = 1024
CHECK_PAGE_DESCRIPTION_LIMIT = 3600

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


def _limited_lines(lines: list[str], *, limit: int = EMBED_FIELD_VALUE_LIMIT) -> str:
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


async def list_related_kukais(session: AsyncSession, guild_id: int, user_id: int) -> list[Kukai]:
    kukai_ids: set[int] = set()
    queries = [
        select(Kukai.id).where(Kukai.guild_id == guild_id, Kukai.created_by == user_id),
        select(KukaiAdmin.kukai_id).join(Kukai).where(Kukai.guild_id == guild_id, KukaiAdmin.user_id == user_id),
        select(Entry.kukai_id).join(Kukai).where(Kukai.guild_id == guild_id, Entry.user_id == user_id),
        select(Submission.kukai_id)
        .join(Kukai)
        .where(
            Kukai.guild_id == guild_id,
            Submission.user_id == user_id,
            Submission.is_discarded.is_(False),
        ),
        select(Select.kukai_id).join(Kukai).where(Kukai.guild_id == guild_id, Select.selector_user_id == user_id),
        select(OverallSelectComment.kukai_id)
        .join(Kukai)
        .where(Kukai.guild_id == guild_id, OverallSelectComment.user_id == user_id),
    ]
    for query in queries:
        result = await session.execute(query)
        kukai_ids.update(int(row[0]) for row in result.all())

    if not kukai_ids:
        return []

    result = await session.execute(
        select(Kukai)
        .where(
            Kukai.id.in_(kukai_ids),
            Kukai.guild_id == guild_id,
            Kukai.state.notin_([KukaiState.ENDED.value, KukaiState.CANCELLED.value]),
        )
        .order_by(Kukai.created_at.desc(), Kukai.id.desc())
    )
    return list(result.scalars().all())


async def build_check_pages(session: AsyncSession, kukais: list[Kukai], user_id: int) -> list[discord.Embed]:
    if not kukais:
        return [
            discord.Embed(
                description="確認できる参加中の句会はありません。",
                color=COLOR_INFO,
            )
        ]

    lines: list[str] = []
    for index, kukai in enumerate(kukais):
        if index:
            lines.append("")
        lines.extend(await _check_lines_for_kukai(session, kukai, user_id))

    descriptions = _paginate_lines(lines, limit=CHECK_PAGE_DESCRIPTION_LIMIT)
    pages: list[discord.Embed] = []
    total = len(descriptions)
    for index, description in enumerate(descriptions, start=1):
        embed = discord.Embed(
            title="参加状況",
            description=description,
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"ページ {index}/{total}")
        pages.append(embed)
    return pages


async def _check_lines_for_kukai(session: AsyncSession, kukai, user_id: int) -> list[str]:
    state = KukaiState.from_value(kukai.state)
    state_ja = STATE_LABEL.get(kukai.state, kukai.state)
    lines = [f"**[{kukai.id}] {discord_safe(kukai.title)}** - {state_ja}"]
    deadline_parts = []
    if kukai.submission_close_at:
        deadline_parts.append(f"投句締切: {format_jst(kukai.submission_close_at)}")
    if kukai.selecting_close_at:
        deadline_parts.append(f"選句締切: {format_jst(kukai.selecting_close_at)}")
    if deadline_parts:
        lines.append(" / ".join(deadline_parts))

    entry = None
    if kukai.entry_enabled:
        entry = await entry_repo.get_by_user(session, kukai.id, user_id)
        if entry:
            status_ja = {
                "approved": "承認済み",
                "pending": "承認待ち",
                "rejected": "却下",
                "withdrawn": "取消済み",
            }.get(entry.status, entry.status)
            lines.append(f"エントリー: {status_ja}")
        else:
            lines.append("エントリー: （未エントリー）")

    subs = await submission_repo.get_user_submissions(session, kukai.id, user_id)
    if state in {
        KukaiState.SUBMISSION_OPEN,
        KukaiState.SUBMISSION_CLOSED,
        KukaiState.WAITING_PUBLISH,
        KukaiState.SELECTING_OPEN,
        KukaiState.SELECTING_CLOSED,
        KukaiState.RESULTS,
        KukaiState.ENDED,
    }:
        max_count = "∞" if kukai.submission_max is None else str(kukai.submission_max)
        lines.append(f"投句 ({len(subs)}/{max_count}):")
        if subs:
            lines.extend(
                f"`{i + 1}.` {discord_safe_submission_text(sub.text)}"
                for i, sub in enumerate(subs)
            )
        else:
            lines.append("（未投句）")

    if state in {
        KukaiState.SELECTING_OPEN,
        KukaiState.SELECTING_CLOSED,
        KukaiState.RESULTS,
        KukaiState.ENDED,
    }:
        select_lines = await _select_status_lines(session, kukai, user_id)
        lines.extend(select_lines)

    return lines


async def _select_status_lines(session: AsyncSession, kukai, user_id: int) -> list[str]:
    selects = await select_repo.get_selects_by_selector(session, kukai.id, user_id)
    overall = await select_repo.get_overall_comment(session, kukai.id, user_id)

    result = await session.execute(
        select(SelectLabel)
        .where(SelectLabel.kukai_id == kukai.id)
        .order_by(SelectLabel.display_order)
    )
    label_map = {lbl.id: lbl for lbl in result.scalars().all()}

    pub_subs = await submission_repo.list_published(session, kukai.id)
    pub_map = {ps.submission_id: ps for ps in pub_subs}

    lines = [f"選句 ({len(selects)}票):"]
    if selects:
        for sel in selects:
            ps = pub_map.get(sel.submission_id)
            num = f"No.{ps.number}" if ps else f"sub:{sel.submission_id}"
            lbl = label_map.get(sel.select_label_id)
            lbl_name = "作者コメント" if sel.is_self_comment else (lbl.label if lbl else "?")
            comment_part = ""
            if sel.comment:
                comment_part = f" - {discord_safe(sel.comment.comment[:80])}"
            lines.append(f"{num} **{lbl_name}**{comment_part}")
    else:
        lines.append("（未選句）")

    if overall:
        lines.append(f"総評: {discord_safe(overall.comment[:200])}")
    return lines


def _paginate_lines(lines: list[str], *, limit: int = CHECK_PAGE_DESCRIPTION_LIMIT) -> list[str]:
    pages: list[str] = []
    current: list[str] = []
    current_len = 0
    for raw_line in lines:
        line = _trim_line(raw_line, limit=limit)
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > limit:
            pages.append("\n".join(current))
            current = [line]
            current_len = len(line)
            continue
        current.append(line)
        current_len += extra
    if current:
        pages.append("\n".join(current))
    return pages or ["（表示できる項目がありません）"]


def _trim_line(line: str, *, limit: int) -> str:
    if len(line) <= limit:
        return line
    return line[: max(0, limit - 1)] + "…"


async def build_check_embed(session: AsyncSession, kukai, user_id: int) -> discord.Embed:
    state = KukaiState.from_value(kukai.state)

    entry = None
    if kukai.entry_enabled:
        entry = await entry_repo.get_by_user(session, kukai.id, user_id)

    subs = await submission_repo.get_user_submissions(session, kukai.id, user_id)
    selects = await select_repo.get_selects_by_selector(session, kukai.id, user_id)
    overall = await select_repo.get_overall_comment(session, kukai.id, user_id)

    result = await session.execute(
        select(SelectLabel)
        .where(SelectLabel.kukai_id == kukai.id)
        .order_by(SelectLabel.display_order)
    )
    label_map = {lbl.id: lbl for lbl in result.scalars().all()}

    pub_subs = await submission_repo.list_published(session, kukai.id)
    pub_map = {ps.submission_id: ps for ps in pub_subs}

    embed = discord.Embed(
        title=f"参加状況 - {kukai.title}",
        color=COLOR_INFO,
    )
    embed.set_footer(text=f"句会 ID: {kukai.id} | 状態: {kukai.state}")

    if kukai.entry_enabled:
        if entry:
            status_ja = {
                "approved": "承認済み",
                "pending": "承認待ち",
                "rejected": "却下",
                "withdrawn": "取消済み",
            }.get(entry.status, entry.status)
            embed.add_field(name="エントリー", value=status_ja, inline=True)
        else:
            embed.add_field(name="エントリー", value="（未エントリー）", inline=True)

    if state in {
        KukaiState.SUBMISSION_OPEN,
        KukaiState.SUBMISSION_CLOSED,
        KukaiState.WAITING_PUBLISH,
        KukaiState.SELECTING_OPEN,
        KukaiState.SELECTING_CLOSED,
        KukaiState.RESULTS,
        KukaiState.ENDED,
    }:
        if subs:
            lines = [f"`{i + 1}.` {discord_safe_submission_text(s.text)}" for i, s in enumerate(subs)]
            embed.add_field(
                name=f"投句 ({len(subs)}/{kukai.submission_max})",
                value=_limited_lines(lines),
                inline=False,
            )
        else:
            embed.add_field(name="投句", value="（未投句）", inline=False)

    if state in {
        KukaiState.SELECTING_OPEN,
        KukaiState.SELECTING_CLOSED,
        KukaiState.RESULTS,
        KukaiState.ENDED,
    }:
        if selects:
            lines = []
            for sel in selects:
                ps = pub_map.get(sel.submission_id)
                num = f"No.{ps.number}" if ps else f"sub:{sel.submission_id}"
                lbl = label_map.get(sel.select_label_id)
                lbl_name = lbl.label if lbl else "?"
                comment_part = ""
                if sel.comment:
                    comment_part = f" - {discord_safe(sel.comment.comment[:30])}"
                lines.append(f"{num} **{lbl_name}**{comment_part}")
            embed.add_field(name=f"選句 ({len(selects)}票)", value=_limited_lines(lines), inline=False)
        else:
            embed.add_field(name="選句", value="（未選句）", inline=False)

        if overall:
            embed.add_field(name="総評", value=discord_safe(overall.comment[:200]), inline=False)

    return embed
