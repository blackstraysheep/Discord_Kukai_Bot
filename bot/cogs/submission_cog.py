"""Submission command: /submit"""

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import kukai_service, submission_service
from bot.utils.channel import effective_channel_id
from bot.services.errors import ServiceError
from bot.ui.submission_view import SubmissionView, _submissions_embed
from bot.utils.embed_builder import error_embed


class SubmissionCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="submit", description="投句します（追加・編集・削除）")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def submit(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                subs = await submission_service.list_user_submissions(
                    session, kukai.id, interaction.user.id
                )
            embed = _submissions_embed(kukai, subs)
            view = SubmissionView(kukai.id, subs, kukai)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @app_commands.command(name="submit-bulk", description="複数行をまとめて投句します")
    @app_commands.describe(
        kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）",
        texts="1行1句で入力",
    )
    async def submit_bulk(
        self,
        interaction: discord.Interaction,
        texts: str,
        kukai_id: int | None = None,
    ) -> None:
        assert interaction.guild is not None
        poems = [line.strip() for line in texts.splitlines() if line.strip()]
        if not poems:
            await interaction.response.send_message(
                embed=error_embed("1行以上入力してください。"),
                ephemeral=True,
            )
            return

        accepted = 0
        over_limit_count = 0
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                for poem in poems:
                    _, over_limit = await submission_service.submit(
                        session, kukai, interaction.user.id, poem
                    )
                    accepted += 1
                    if over_limit:
                        over_limit_count += 1

                subs = await submission_service.list_user_submissions(
                    session, kukai.id, interaction.user.id
                )

            embed = _submissions_embed(kukai, subs)
            embed.description = f"{accepted}句を登録しました。\n\n{embed.description or ''}"
            if over_limit_count:
                embed.description += (
                    f"\n⚠️ {over_limit_count}句は上限（{kukai.submission_max}句）超過扱いです。"
                )
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SubmissionCog(bot))
