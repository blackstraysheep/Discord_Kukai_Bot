"""Wizard step 3: Schedule (entry_close, submission_close, selecting_close)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard
from bot.utils.datetime_utils import JST, format_jst, parse_datetime


def _placeholder_datetime(*, days_from_now: int, hour: int, minute: int) -> str:
    now_jst = datetime.now(JST)
    candidate = (now_jst + timedelta(days=days_from_now)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now_jst:
        candidate += timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M")


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    filled = bool(state.submission_close_at and state.selecting_close_at)
    embed = discord.Embed(
        title=f"ステップ 3/{STEP_COUNT}: 締切設定",
        color=discord.Color.blurple(),
    )
    sub_str = format_jst(state.submission_close_at) if state.submission_close_at else "（未入力）"
    selecting_str = (
        format_jst(state.selecting_close_at) if state.selecting_close_at else "（未入力）"
    )
    if state.entry_enabled:
        entry_str = format_jst(state.entry_close_at) if state.entry_close_at else "（未入力）"
        embed.add_field(name="エントリー締切", value=entry_str, inline=False)
    embed.add_field(name="投句締切", value=sub_str, inline=False)
    embed.add_field(name="選句締切", value=selecting_str, inline=False)
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
        self.state.step = 2
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 4
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)


class StepScheduleModal(discord.ui.Modal, title="日程の入力"):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        from bot.utils.datetime_utils import to_jst

        self.entry_close: discord.ui.TextInput | None = None
        if state.entry_enabled:
            self.entry_close = discord.ui.TextInput(
                label="エントリー締切 (YYYY-MM-DD HH:MM)",
                placeholder=_placeholder_datetime(days_from_now=7, hour=20, minute=0),
                required=False,
                max_length=20,
            )
            if state.entry_close_at:
                jst = to_jst(state.entry_close_at)
                self.entry_close.default = jst.strftime("%Y-%m-%d %H:%M")
            else:
                self.entry_close.default = _placeholder_datetime(days_from_now=7, hour=20, minute=0)
            self.add_item(self.entry_close)

        self.submission_close = discord.ui.TextInput(
            label="投句締切 (YYYY-MM-DD HH:MM)",
            placeholder=_placeholder_datetime(days_from_now=7, hour=23, minute=59),
            max_length=20,
        )
        if state.submission_close_at:
            jst = to_jst(state.submission_close_at)
            self.submission_close.default = jst.strftime("%Y-%m-%d %H:%M")
        else:
            self.submission_close.default = _placeholder_datetime(days_from_now=7, hour=23, minute=59)
        self.add_item(self.submission_close)

        self.selecting_close = discord.ui.TextInput(
            label="選句締切 (YYYY-MM-DD HH:MM)",
            placeholder=_placeholder_datetime(days_from_now=14, hour=23, minute=59),
            max_length=20,
        )
        if state.selecting_close_at:
            jst = to_jst(state.selecting_close_at)
            self.selecting_close.default = jst.strftime("%Y-%m-%d %H:%M")
        else:
            self.selecting_close.default = _placeholder_datetime(days_from_now=14, hour=23, minute=59)
        self.add_item(self.selecting_close)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        entry_raw = self.entry_close.value.strip() if self.entry_close is not None else ""
        try:
            entry_close = parse_datetime(entry_raw) if entry_raw else None
            sub_close = parse_datetime(self.submission_close.value)
            selecting_close = parse_datetime(self.selecting_close.value)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if entry_close is not None and entry_close <= now:
            await interaction.response.send_message(
                "エントリー締切は現在時刻より未来に設定してください。", ephemeral=True
            )
            return
        if sub_close <= now:
            await interaction.response.send_message(
                "投句締切は現在時刻より未来に設定してください。", ephemeral=True
            )
            return
        if selecting_close <= now:
            await interaction.response.send_message(
                "選句締切は現在時刻より未来に設定してください。", ephemeral=True
            )
            return

        if entry_close is not None and sub_close < entry_close:
            await interaction.response.send_message(
                "投句締切はエントリー締切以降に設定してください。", ephemeral=True
            )
            return
        if selecting_close <= sub_close:
            await interaction.response.send_message(
                "選句締切は投句締切より後に設定してください。", ephemeral=True
            )
            return

        self.state.entry_close_at = entry_close if self.state.entry_enabled else None
        self.state.submission_close_at = sub_close
        self.state.selecting_close_at = selecting_close
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)
