"""Wizard step 2: Schedule (submission_close, voting_close)."""

from __future__ import annotations

import discord

from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard
from bot.utils.datetime_utils import format_jst, parse_datetime


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    filled = bool(state.submission_close_at and state.voting_close_at)
    embed = discord.Embed(
        title=f"ステップ 2/{STEP_COUNT}: 日程",
        color=discord.Color.blurple(),
    )
    sub_str = format_jst(state.submission_close_at) if state.submission_close_at else "（未入力）"
    vote_str = format_jst(state.voting_close_at) if state.voting_close_at else "（未入力）"
    embed.add_field(name="投句締切", value=sub_str, inline=False)
    embed.add_field(name="選句締切", value=vote_str, inline=False)
    embed.set_footer(text="書式: YYYY-MM-DD HH:MM（JST）")
    return embed, StepScheduleView(state, filled=filled)


class StepScheduleView(discord.ui.View):
    def __init__(self, state: WizardState, *, filled: bool) -> None:
        super().__init__(timeout=900)
        self.state = state

        fill_btn = discord.ui.Button(
            label="📅 日程を入力",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        fill_btn.callback = self._fill
        self.add_item(fill_btn)

        back_btn = discord.ui.Button(
            label="← 戻る",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        back_btn.callback = self._back
        self.add_item(back_btn)

        next_btn = discord.ui.Button(
            label="次へ ➜",
            style=discord.ButtonStyle.success,
            disabled=not filled,
            row=1,
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

        cancel_btn = discord.ui.Button(
            label="❌ キャンセル",
            style=discord.ButtonStyle.danger,
            row=1,
        )
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _fill(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(StepScheduleModal(self.state))

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 1
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 3
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)


class StepScheduleModal(discord.ui.Modal, title="日程の入力"):
    submission_close = discord.ui.TextInput(
        label="投句締切 *  (YYYY-MM-DD HH:MM)",
        placeholder="2026-06-01 23:59",
        max_length=20,
    )
    voting_close = discord.ui.TextInput(
        label="選句締切 *  (YYYY-MM-DD HH:MM)",
        placeholder="2026-06-08 23:59",
        max_length=20,
    )

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        if state.submission_close_at:
            from bot.utils.datetime_utils import to_jst
            jst = to_jst(state.submission_close_at)
            self.submission_close.default = jst.strftime("%Y-%m-%d %H:%M")
        if state.voting_close_at:
            from bot.utils.datetime_utils import to_jst
            jst = to_jst(state.voting_close_at)
            self.voting_close.default = jst.strftime("%Y-%m-%d %H:%M")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            sub_close = parse_datetime(self.submission_close.value)
            vote_close = parse_datetime(self.voting_close.value)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        if vote_close <= sub_close:
            await interaction.response.send_message(
                "選句締切は投句締切より後に設定してください。", ephemeral=True
            )
            return

        self.state.submission_close_at = sub_close
        self.state.voting_close_at = vote_close
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)
