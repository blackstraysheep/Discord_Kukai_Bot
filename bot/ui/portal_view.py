"""Persistent server hub view for common kukai entry points."""

from __future__ import annotations

import discord

from bot.database import get_session
from bot.services import kukai_list_view
from bot.services import check_service, kukai_service, permission_service, select_rule_service
from bot.services.errors import ServiceError
from bot.utils.embed_builder import COLOR_INFO, error_embed


PORTAL_CREATE_CUSTOM_ID = "kukai:portal:create"
PORTAL_LIST_CUSTOM_ID = "kukai:portal:list"
PORTAL_CHECK_CUSTOM_ID = "kukai:portal:check"


def build_portal_embed() -> discord.Embed:
    embed = discord.Embed(
        title="句会案内",
        description="句会の作成、一覧確認、自分の参加状況確認はこちらから行えます。",
        color=COLOR_INFO,
    )
    return embed


class PortalView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="句会を作成",
        style=discord.ButtonStyle.primary,
        custom_id=PORTAL_CREATE_CUSTOM_ID,
    )
    async def create_kukai(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            allowed = await permission_service.can_create_kukai(
                session,
                interaction.guild.id,
                interaction.user,  # type: ignore[arg-type]
            )
            templates = await select_rule_service.list_templates(session, interaction.guild.id)
        if not allowed:
            await interaction.followup.send(embed=error_embed("句会の作成権限がありません。"), ephemeral=True)
            return

        from bot.ui.wizard.start import build_create_wizard_state, send_create_wizard_followup

        state = await build_create_wizard_state(
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
            templates=templates,
        )
        await send_create_wizard_followup(interaction, state)

    @discord.ui.button(
        label="句会一覧",
        style=discord.ButtonStyle.secondary,
        custom_id=PORTAL_LIST_CUSTOM_ID,
    )
    async def list_kukais(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_session() as session:
            kukais = await kukai_service.list_kukais(session, interaction.guild.id)

        await interaction.response.send_message(
            embed=kukai_list_view.build_kukai_list_embed(kukais),
            ephemeral=True,
        )

    @discord.ui.button(
        label="自分の状況",
        style=discord.ButtonStyle.secondary,
        custom_id=PORTAL_CHECK_CUSTOM_ID,
    )
    async def check_status(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_session() as session:
            kukais = await kukai_service.list_kukais(session, interaction.guild.id)

        if not kukais:
            await interaction.response.send_message(
                embed=discord.Embed(description="確認できる句会がありません。", color=COLOR_INFO),
                ephemeral=True,
            )
            return
        if len(kukais) == 1:
            await _send_check_embed(interaction, kukais[0].id)
            return
        await interaction.response.send_message(
            embed=discord.Embed(description="状況を確認する句会を選んでください。", color=COLOR_INFO),
            view=KukaiSelectForCheckView(user_id=interaction.user.id, kukais=kukais[:25]),
            ephemeral=True,
        )


class KukaiSelectForCheckView(discord.ui.View):
    def __init__(self, *, user_id: int, kukais: list) -> None:
        super().__init__(timeout=120)
        self.user_id = user_id
        options = [
            discord.SelectOption(label=f"[{kukai.id}] {kukai.title}"[:100], value=str(kukai.id))
            for kukai in kukais
        ]
        select = discord.ui.Select(placeholder="句会を選択", options=options)

        async def _callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    embed=error_embed("この選択UIは呼び出した本人だけが操作できます。"),
                    ephemeral=True,
                )
                return
            await _send_check_embed(interaction, int(select.values[0]))

        select.callback = _callback
        self.add_item(select)


async def _send_check_embed(interaction: discord.Interaction, kukai_id: int) -> None:
    assert interaction.guild is not None
    try:
        async with get_session() as session:
            kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
            embed = await check_service.build_check_embed(session, kukai, interaction.user.id)
    except ServiceError as error:
        embed = error_embed(str(error))
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=None)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)
