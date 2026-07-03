"""Persistent server hub view for common kukai entry points."""

from __future__ import annotations

import discord

from bot.database import get_session
from bot.services import check_service, kukai_service, permission_service, select_rule_service
from bot.services.errors import ServiceError
from bot.utils.datetime_utils import format_jst
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

        from bot.ui.wizard.base import goto_step
        from bot.ui.wizard.wizard_state import WizardState, set_wizard

        state = WizardState(user_id=interaction.user.id, guild_id=interaction.guild.id)
        state.select_preset_options = [{"id": t.id, "name": t.name} for t in templates]
        default_template = next((t for t in templates if t.is_default), None)
        if default_template is not None:
            points_enabled, _ = select_rule_service.deserialize_template_payload(
                default_template.definition_json
            )
            state.select_preset_template_id = default_template.id
            state.select_preset_name = default_template.name
            state.select_points_enabled = points_enabled
            state.select_label_specs = select_rule_service.build_kukai_specs_from_template(
                default_template
            )
        else:
            state.select_label_specs = select_rule_service.default_kukai_specs()
        state.selected_select_label = next(
            (
                str(spec["label"])
                for spec in state.select_label_specs
                if spec["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
            ),
            "特選",
        )
        set_wizard(state)
        await goto_step(interaction, state, first_send=True)

    @discord.ui.button(
        label="句会一覧",
        style=discord.ButtonStyle.secondary,
        custom_id=PORTAL_LIST_CUSTOM_ID,
    )
    async def list_kukais(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_session() as session:
            kukais = await kukai_service.list_kukais(session, interaction.guild.id)

        if not kukais:
            embed = discord.Embed(
                description="現在、開催中または招集中の句会はありません。",
                color=COLOR_INFO,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(title="句会一覧", color=COLOR_INFO)
        for kukai in kukais[:10]:
            lines = [f"状態: {kukai.state}"]
            if kukai.channel_id:
                lines.append(f"チャンネル: <#{kukai.channel_id}>")
            if kukai.submission_close_at:
                lines.append(f"投句締切: {format_jst(kukai.submission_close_at)}")
            if kukai.selecting_close_at:
                lines.append(f"選句締切: {format_jst(kukai.selecting_close_at)}")
            embed.add_field(name=f"[{kukai.id}] {kukai.title}", value="\n".join(lines), inline=False)
        if len(kukais) > 10:
            embed.set_footer(text=f"他 {len(kukais) - 10} 件")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
