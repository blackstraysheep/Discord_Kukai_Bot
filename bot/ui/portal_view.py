"""Persistent server hub view for common kukai entry points."""

from __future__ import annotations

import discord

from bot.database import get_session
from bot.models.guild_settings import GuildSettings
from bot.services import kukai_list_view
from bot.services import check_service, kukai_service, permission_service, select_rule_service
from bot.ui.check_view import CheckPagerView
from bot.ui.participation_record_view import ParticipationRecordOptionsView
from bot.utils.embed_builder import COLOR_INFO, error_embed


PORTAL_CREATE_CUSTOM_ID = "kukai:portal:create"
PORTAL_LIST_CUSTOM_ID = "kukai:portal:list"
PORTAL_CHECK_CUSTOM_ID = "kukai:portal:check"
PORTAL_RECORD_CUSTOM_ID = "kukai:portal:record"


def build_portal_embed() -> discord.Embed:
    embed = discord.Embed(
        title="句会案内",
        description="句会の作成、一覧確認、参加状況・参加記録の確認はこちらから行えます。",
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
            kukais = await check_service.list_related_kukais(
                session,
                interaction.guild.id,
                interaction.user.id,
            )
            pages = await check_service.build_check_pages(session, kukais, interaction.user.id)

        await interaction.response.send_message(
            embed=pages[0],
            view=CheckPagerView.for_pages(user_id=interaction.user.id, pages=pages),
            ephemeral=True,
        )

    @discord.ui.button(
        label="参加の記録",
        style=discord.ButtonStyle.secondary,
        custom_id=PORTAL_RECORD_CUSTOM_ID,
    )
    async def participation_record(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        async with get_session() as session:
            settings = await session.get(GuildSettings, interaction.guild.id)
            allow_other = bool(
                settings is not None
                and settings.participation_record_visibility == "guild_public"
            )
        view = ParticipationRecordOptionsView(
            bot=interaction.client,
            guild=interaction.guild,
            user=interaction.user,
            allow_other=allow_other,
        )
        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
        )
