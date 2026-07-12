"""Wizard navigation helpers."""

from __future__ import annotations

import discord

from bot.database import get_session
from bot.repositories import notification_preset_repo
from bot.services import notification_preset_service
from bot.ui.wizard.wizard_state import WizardState, set_wizard

STEP_COUNT = 9
EXIT_NOTE = "中止する場合はこのメッセージを削除してください。"
STEP_NAMES = {
    1: "基本情報",
    2: "エントリー設定",
    3: "締切設定",
    4: "投句設定",
    5: "選句設定",
    6: "公開・結果設定",
    7: "ボイス句会設定",
    8: "通知設定",
    9: "確認",
}


def step_header(step: int) -> str:
    return f"ステップ {step}/{STEP_COUNT}: {STEP_NAMES.get(step, '')}"


async def goto_step(
    interaction: discord.Interaction,
    state: WizardState,
    *,
    first_send: bool = False,
) -> None:
    """Render the step view for state.step. Use first_send=True from the initial slash command."""
    embed, view = await render_step(state)
    if first_send:
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


async def render_step(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    if state.step == 8:
        await _ensure_notify_presets(state)
    embed, view = _make_step(state)
    _append_exit_note(embed)
    return embed, view


def _append_exit_note(embed: discord.Embed) -> None:
    footer_text = embed.footer.text or ""
    if EXIT_NOTE in footer_text:
        return
    text = f"{footer_text} / {EXIT_NOTE}" if footer_text else EXIT_NOTE
    embed.set_footer(text=text)


async def _ensure_notify_presets(state: WizardState) -> None:
    async with get_session() as session:
        presets = await notification_preset_repo.get_by_guild(session, state.guild_id)
        state.notify_preset_options = [
            {"id": p.id, "name": p.name, "is_default": p.is_default}
            for p in presets
        ]
        if not state.notification_specs:
            default_preset = await notification_preset_repo.get_default(session, state.guild_id)
            if default_preset is not None:
                default_entries = notification_preset_service.entries_from_json(
                    default_preset.entries_json
                )
                if default_entries:
                    state.notification_specs = default_entries
                    state.notify_preset_name = default_preset.name
    set_wizard(state)


def _make_step(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    # lazy imports to avoid circular references
    if state.step == 1:
        from bot.ui.wizard.step_basic import build
    elif state.step == 2:
        from bot.ui.wizard.step_entry import build
    elif state.step == 3:
        from bot.ui.wizard.step_schedule import build
    elif state.step == 4:
        from bot.ui.wizard.step_submission import build
    elif state.step == 5:
        from bot.ui.wizard.step_select_rule import build
    elif state.step == 6:
        from bot.ui.wizard.step_publish import build
    elif state.step == 7:
        from bot.ui.wizard.step_voice import build
    elif state.step == 8:
        from bot.ui.wizard.step_notify import build
    elif state.step == 9:
        from bot.ui.wizard.step_confirm import build
    else:
        raise ValueError(f"Unknown wizard step: {state.step}")
    return build(state)  # type: ignore[possibly-undefined]

