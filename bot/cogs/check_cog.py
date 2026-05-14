"""Status check command: /check"""

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from bot.database import get_session
from bot.models.vote_rule import VoteLabel
from bot.repositories import entry_repo, submission_repo, vote_repo
from bot.services import kukai_service
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.utils.embed_builder import COLOR_INFO, error_embed
from bot.utils.text import discord_safe


class CheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="check", description="句会における自分の参加・投句・選句状況を確認します")
    @app_commands.describe(kukai_id="句会ID")
    async def check(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                user_id = interaction.user.id
                state = KukaiState(kukai.state)

                # Entry status
                entry = None
                if kukai.entry_enabled:
                    entry = await entry_repo.get_by_user(session, kukai.id, user_id)

                # Submission status
                subs = await submission_repo.get_user_submissions(session, kukai.id, user_id)

                # Vote status
                votes = await vote_repo.get_votes_by_voter(session, kukai.id, user_id)
                overall = await vote_repo.get_overall_comment(session, kukai.id, user_id)

                # Label map
                result = await session.execute(
                    select(VoteLabel)
                    .where(VoteLabel.kukai_id == kukai.id)
                    .order_by(VoteLabel.display_order)
                )
                label_map = {lbl.id: lbl for lbl in result.scalars().all()}

                # Published subs for vote display
                pub_subs = await submission_repo.list_published(session, kukai.id)
                pub_map = {ps.submission_id: ps for ps in pub_subs}

        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📋 参加状況 — {kukai.title}",
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"句会 ID: {kukai.id}　|　状態: {kukai.state}")

        # Entry field
        if kukai.entry_enabled:
            if entry:
                status_ja = {
                    "approved": "✅ 承認済み",
                    "pending": "⏳ 審査待ち",
                    "rejected": "❌ 却下",
                    "withdrawn": "↩️ 取消済み",
                }.get(entry.status, entry.status)
                embed.add_field(name="エントリー", value=status_ja, inline=True)
            else:
                embed.add_field(name="エントリー", value="（未エントリー）", inline=True)

        # Submission field
        if state in {
            KukaiState.SUBMISSION_OPEN, KukaiState.SUBMISSION_CLOSED,
            KukaiState.WAITING_PUBLISH, KukaiState.VOTING_OPEN,
            KukaiState.VOTING_CLOSED, KukaiState.WAITING_RESULTS,
            KukaiState.RESULTS, KukaiState.ENDED,
        }:
            if subs:
                lines = [f"`{i + 1}.` {discord_safe(s.text)}" for i, s in enumerate(subs)]
                embed.add_field(
                    name=f"投句 ({len(subs)}/{kukai.submission_max})",
                    value="\n".join(lines),
                    inline=False,
                )
            else:
                embed.add_field(name="投句", value="（未投句）", inline=False)

        # Vote field
        if state in {
            KukaiState.VOTING_OPEN, KukaiState.VOTING_CLOSED,
            KukaiState.WAITING_RESULTS, KukaiState.RESULTS, KukaiState.ENDED,
        }:
            if votes:
                lines = []
                for v in votes:
                    ps = pub_map.get(v.submission_id)
                    num = f"No.{ps.number}" if ps else f"sub:{v.submission_id}"
                    lbl = label_map.get(v.vote_label_id)
                    lbl_name = lbl.label if lbl else "?"
                    comment_part = ""
                    if v.comment:
                        comment_part = f" — {discord_safe(v.comment.comment[:30])}"
                    lines.append(f"{num} **{lbl_name}**{comment_part}")
                embed.add_field(
                    name=f"選句 ({len(votes)}票)",
                    value="\n".join(lines),
                    inline=False,
                )
            else:
                embed.add_field(name="選句", value="（未選句）", inline=False)

            if overall:
                embed.add_field(
                    name="総評",
                    value=discord_safe(overall.comment[:200]),
                    inline=False,
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CheckCog(bot))
