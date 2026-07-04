"""Create-kukai wizard startup helpers shared by commands and portal buttons."""

from __future__ import annotations

import discord

from bot.services import select_rule_service
from bot.ui.wizard.base import render_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard


async def build_create_wizard_state(*, guild_id: int, user_id: int, templates: list) -> WizardState:
    state = WizardState(user_id=user_id, guild_id=guild_id)
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
    return state


async def send_create_wizard_followup(interaction: discord.Interaction, state: WizardState) -> None:
    embed, view = await render_step(state)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
