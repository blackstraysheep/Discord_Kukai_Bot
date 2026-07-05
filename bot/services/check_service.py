"""Build personal kukai participation status embeds."""

from __future__ import annotations

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.select_rule import SelectLabel
from bot.repositories import entry_repo, select_repo, submission_repo
from bot.state_machine.states import KukaiState
from bot.utils.embed_builder import COLOR_INFO
from bot.utils.submission_markup import discord_safe_submission_text
from bot.utils.text import discord_safe


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
                value="\n".join(lines),
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
            embed.add_field(name=f"選句 ({len(selects)}票)", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="選句", value="（未選句）", inline=False)

        if overall:
            embed.add_field(name="総評", value=discord_safe(overall.comment[:200]), inline=False)

    return embed
