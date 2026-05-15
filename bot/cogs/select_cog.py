"""Selecting command: /select"""

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.models.select_rule import SelectLabel
from bot.services import kukai_service
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.ui.select_view import SelectView, load_select_data
from bot.utils.embed_builder import COLOR_INFO, error_embed


class SelectCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="select", description="選句します（投句を一覧表示して選句）")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def select(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=interaction.channel_id,
                    kukai_id=kukai_id,
                )
                if KukaiState.from_value(kukai.state) != KukaiState.SELECTING_OPEN:
                    await interaction.response.send_message(
                        embed=error_embed("現在選句を受け付けていません。"), ephemeral=True
                    )
                    return

                pub_subs, labels, selects_by_sub, overall_comment = await load_select_data(
                    session, kukai.id, interaction.user.id
                )
                if not any(lbl.label == "作者コメント" for lbl in labels):
                    session.add(
                        SelectLabel(
                            kukai_id=kukai.id,
                            template_id=None,
                            display_order=999,
                            label="作者コメント",
                            point=0,
                            rank_priority=999,
                            min_count=0,
                            max_count=None,
                            comment_mode="required",
                        )
                    )
                    await session.flush()
                    pub_subs, labels, selects_by_sub, overall_comment = await load_select_data(
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

            view = SelectView(
                kukai,
                pub_subs,
                labels,
                selects_by_sub,
                overall_comment=overall_comment,
                selector_user_id=interaction.user.id,
            )
            await interaction.response.send_message(
                embed=view.build_embed(), view=view, ephemeral=True
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelectCog(bot))
