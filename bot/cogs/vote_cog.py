"""Voting command: /select"""

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import kukai_service
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.ui.vote_view import VoteView, load_vote_data
from bot.utils.embed_builder import COLOR_INFO, error_embed


class VoteCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="select", description="選句します（投句を一覧表示して選句）")
    @app_commands.describe(kukai_id="句会ID")
    async def select(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if KukaiState(kukai.state) != KukaiState.VOTING_OPEN:
                    await interaction.response.send_message(
                        embed=error_embed("現在選句を受け付けていません。"), ephemeral=True
                    )
                    return

                pub_subs, labels, votes_by_sub = await load_vote_data(
                    session, kukai.id, interaction.user.id
                )

            if not pub_subs:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="公開済みの投句がありません。",
                        color=COLOR_INFO,
                    ),
                    ephemeral=True,
                )
                return

            if not labels:
                await interaction.response.send_message(
                    embed=error_embed("選句ラベルが設定されていません。管理者にお問い合わせください。"),
                    ephemeral=True,
                )
                return

            view = VoteView(
                kukai, pub_subs, labels, votes_by_sub,
                idx=0, voter_user_id=interaction.user.id
            )
            await interaction.response.send_message(
                embed=view.build_embed(), view=view, ephemeral=True
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoteCog(bot))
