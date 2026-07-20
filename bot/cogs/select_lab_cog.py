"""Experimental selection interfaces: /select-lab."""

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import kukai_service, select_lab_service
from bot.services.errors import ServiceError, ValidationError
from bot.state_machine.states import KukaiState
from bot.ui.select_lab import SelectLabFormModal, build_batch_response, build_review_response
from bot.utils.channel import effective_channel_id
from bot.utils.embed_builder import error_embed


class SelectLabCog(commands.Cog):
    select_lab = app_commands.Group(
        name="select-lab",
        description="比較テスト用の選句UI（既存の選句データと共有）",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _load(self, interaction: discord.Interaction, kukai_id: int | None):
        assert interaction.guild is not None
        async with get_session() as session:
            kukai = await kukai_service.resolve_kukai_in_channel(
                session,
                guild_id=interaction.guild.id,
                channel_id=effective_channel_id(interaction),
                kukai_id=kukai_id,
            )
            if KukaiState.from_value(kukai.state) != KukaiState.SELECTING_OPEN:
                raise ValidationError("現在選句を受け付けていません。")
            data = await select_lab_service.load_lab_data(session, kukai.id, interaction.user.id)
            return kukai, data

    @select_lab.command(name="review", description="1句ずつ種別ボタンで高速に選句します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def review(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        try:
            kukai, data = await self._load(interaction, kukai_id)
            embed, view = build_review_response(kukai, data, interaction.user.id)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except ServiceError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)

    @select_lab.command(name="batch", description="選句種別ごとに複数句をまとめて選びます")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def batch(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        try:
            kukai, data = await self._load(interaction, kukai_id)
            embed, view = build_batch_response(kukai, data, interaction.user.id)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except ServiceError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)

    @select_lab.command(name="form", description="全選句・選評・総評を1つのフォームで編集します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def form(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        try:
            kukai, data = await self._load(interaction, kukai_id)
            fields = select_lab_service.serialize_form_fields(data, interaction.user.id)
            await interaction.response.send_modal(
                SelectLabFormModal(kukai, interaction.user.id, fields)
            )
        except ServiceError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelectLabCog(bot))
