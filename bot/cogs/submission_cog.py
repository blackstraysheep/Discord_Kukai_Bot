"""Submission command: /submit"""

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import kukai_service, submission_service
from bot.services.errors import ServiceError
from bot.ui.submission_view import SubmissionView, _submissions_embed
from bot.utils.embed_builder import error_embed


class SubmissionCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="submit", description="投句します（追加・編集・削除）")
    @app_commands.describe(kukai_id="句会ID")
    async def submit(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                subs = await submission_service.list_user_submissions(
                    session, kukai.id, interaction.user.id
                )
            embed = _submissions_embed(kukai, subs)
            view = SubmissionView(kukai_id, subs, kukai)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SubmissionCog(bot))
