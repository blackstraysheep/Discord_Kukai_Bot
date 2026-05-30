"""Wizard step 7: optional voice session settings."""

from __future__ import annotations

import discord

from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard
from bot.utils.datetime_utils import format_jst, parse_datetime, to_jst


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title=f"ステップ 7/{STEP_COUNT}: ボイス句会設定",
        color=discord.Color.blurple(),
    )
    if not state.voice_enabled:
        embed.add_field(name="ボイス句会", value="使用しない", inline=False)
    else:
        lines = [
            f"場所: <#{state.voice_channel_id}>" if state.voice_channel_id else "場所: （未選択）",
            f"開始: {format_jst(state.voice_start_at)}" if state.voice_start_at else "開始: （未入力）",
        ]
        if state.voice_end_at:
            lines.append(f"終了: {format_jst(state.voice_end_at)}")
        embed.add_field(name="ボイス句会", value="\n".join(lines), inline=False)
    return embed, StepVoiceView(state)


class StepVoiceView(discord.ui.View):
    def __init__(self, state: WizardState) -> None:
        super().__init__(timeout=900)
        self.state = state

        toggle_btn = discord.ui.Button(
            label="使用する" if not state.voice_enabled else "使用しない",
            style=discord.ButtonStyle.primary if not state.voice_enabled else discord.ButtonStyle.secondary,
            row=0,
        )
        toggle_btn.callback = self._toggle
        self.add_item(toggle_btn)

        if state.voice_enabled:
            self.add_item(_VoiceChannelSelect(state))
            schedule_btn = discord.ui.Button(label="日時を入力", style=discord.ButtonStyle.primary, row=2)
            schedule_btn.callback = self._schedule
            self.add_item(schedule_btn)

        cancel_btn = discord.ui.Button(label="❌ キャンセル", style=discord.ButtonStyle.danger, row=4)
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

        back_btn = discord.ui.Button(label="← 戻る", style=discord.ButtonStyle.secondary, row=4)
        back_btn.callback = self._back
        self.add_item(back_btn)

        next_disabled = state.voice_enabled and not (state.voice_channel_id and state.voice_start_at)
        next_btn = discord.ui.Button(
            label="次へ ➜",
            style=discord.ButtonStyle.success,
            row=4,
            disabled=next_disabled,
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

    async def _toggle(self, interaction: discord.Interaction) -> None:
        self.state.voice_enabled = not self.state.voice_enabled
        if not self.state.voice_enabled:
            self.state.voice_channel_id = None
            self.state.voice_start_at = None
            self.state.voice_end_at = None
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _schedule(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(VoiceScheduleModal(self.state))

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 6
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 8
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)


class _VoiceChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        kwargs: dict[str, object] = {}
        if state.voice_channel_id:
            kwargs["default_values"] = [discord.Object(id=state.voice_channel_id)]
        super().__init__(
            placeholder="ボイス句会の場所を選択",
            channel_types=[discord.ChannelType.voice, discord.ChannelType.stage_voice],
            min_values=1,
            max_values=1,
            row=1,
            **kwargs,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state.voice_channel_id = int(self.values[0].id)
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class VoiceScheduleModal(discord.ui.Modal, title="ボイス句会日時"):
    start_at = discord.ui.TextInput(
        label="開始日時 * (YYYY-MM-DD HH:MM)",
        placeholder="2026-06-08 21:00",
        max_length=20,
    )
    end_at = discord.ui.TextInput(
        label="終了日時 (YYYY-MM-DD HH:MM)",
        placeholder="2026-06-08 22:00",
        required=False,
        max_length=20,
    )

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        if state.voice_start_at:
            self.start_at.default = to_jst(state.voice_start_at).strftime("%Y-%m-%d %H:%M")
        if state.voice_end_at:
            self.end_at.default = to_jst(state.voice_end_at).strftime("%Y-%m-%d %H:%M")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            start_at = parse_datetime(self.start_at.value)
            end_at = parse_datetime(self.end_at.value) if self.end_at.value.strip() else None
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        if end_at is not None and end_at <= start_at:
            await interaction.response.send_message("終了日時は開始日時より後にしてください。", ephemeral=True)
            return
        self.state.voice_start_at = start_at
        self.state.voice_end_at = end_at
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)
