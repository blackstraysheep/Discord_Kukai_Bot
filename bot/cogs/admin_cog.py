"""Admin commands: /kukai export, /kukai import, /kukai admin *, guild settings"""

import discord
from discord import app_commands
from discord.ext import commands


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    kukai = app_commands.Group(name="kukai_admin", description="句会管理者・エクスポート操作")

    @kukai.command(name="export", description="【管理者】句会データをエクスポートします")
    @app_commands.describe(kukai_id="句会ID（省略で全件）")
    async def export(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        await interaction.response.send_message("🚧 未実装", ephemeral=True)

    @kukai.command(name="import_data", description="【管理者】句会データをインポートします")
    async def import_data(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("🚧 未実装", ephemeral=True)

    @kukai.command(name="add_admin", description="【管理者】句会管理者を追加します")
    @app_commands.describe(kukai_id="句会ID", user="追加するユーザー")
    async def add_admin(
        self, interaction: discord.Interaction, kukai_id: int, user: discord.Member
    ) -> None:
        await interaction.response.send_message("🚧 未実装", ephemeral=True)

    @kukai.command(name="remove_admin", description="【管理者】句会管理者を削除します")
    @app_commands.describe(kukai_id="句会ID", user="削除するユーザー")
    async def remove_admin(
        self, interaction: discord.Interaction, kukai_id: int, user: discord.Member
    ) -> None:
        await interaction.response.send_message("🚧 未実装", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
