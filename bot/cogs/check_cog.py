"""Status check command: /check"""

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import check_service, kukai_service
from bot.utils.channel import effective_channel_id
from bot.services.errors import ServiceError
from bot.utils.embed_builder import error_embed


class CheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="check", description="句会における自分の参加・投句・選句状況を確認します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def check(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                embed = await check_service.build_check_embed(session, kukai, interaction.user.id)

        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CheckCog(bot))
