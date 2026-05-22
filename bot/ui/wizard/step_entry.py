"""Wizard step 2: Entry settings."""

from __future__ import annotations

import discord

from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title=f"ステップ 2/{STEP_COUNT}: エントリー設定",
        color=discord.Color.blurple(),
    )
    enabled_str = "✅ 有効（エントリー制）" if state.entry_enabled else "🚫 無効（全員参加可）"
    if state.entry_enabled:
        approval_str = "✅ 要" if state.entry_approval else "🚫 不要（自動承認）"
        mode_str = "自動" if state.entry_mode in {"auto", "full_auto"} else "手動"
    else:
        approval_str = "（エントリー無効時は常に不要）"
        mode_str = "（エントリー無効時は適用外）"
    embed.add_field(name="エントリー機能", value=enabled_str, inline=False)
    embed.add_field(name="承認制", value=approval_str, inline=True)
    embed.add_field(name="エントリー締切進行", value=mode_str, inline=True)
    embed.set_footer(text="設定後「次へ」で締切設定に進みます。")
    return embed, StepEntryView(state)


class _EntryEnabledSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="エントリー機能",
            options=[
                discord.SelectOption(
                    label="有効（エントリー制）",
                    value="true",
                    default=state.entry_enabled,
                ),
                discord.SelectOption(
                    label="無効（全員参加可）",
                    value="false",
                    default=not state.entry_enabled,
                ),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state.entry_enabled = self.values[0] == "true"
        if not self.state.entry_enabled:
            self.state.entry_approval = False
            self.state.entry_mode = "manual"
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class _EntryApprovalSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="承認制",
            disabled=not state.entry_enabled,
            options=[
                discord.SelectOption(
                    label="承認不要（自動承認）",
                    value="false",
                    default=not state.entry_approval,
                ),
                discord.SelectOption(
                    label="承認制（管理者が承認）",
                    value="true",
                    default=state.entry_approval,
                ),
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.state.entry_enabled:
            await interaction.response.send_message(
                "エントリー無効時は承認制を変更できません。", ephemeral=True
            )
            return
        self.state.entry_approval = self.values[0] == "true"
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class _EntryModeSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="エントリー締切進行",
            disabled=not state.entry_enabled,
            options=[
                discord.SelectOption(
                    label="手動（締切時刻では進行しない）",
                    value="manual",
                    default=state.entry_mode == "manual",
                ),
                discord.SelectOption(
                    label="自動（締切時刻に成立判定）",
                    value="auto",
                    default=state.entry_mode in {"auto", "full_auto"},
                ),
            ],
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.state.entry_enabled:
            await interaction.response.send_message(
                "エントリー無効時は締切進行を変更できません。", ephemeral=True
            )
            return
        self.state.entry_mode = self.values[0]
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class StepEntryView(discord.ui.View):
    def __init__(self, state: WizardState) -> None:
        super().__init__(timeout=900)
        self.state = state
        self.add_item(_EntryEnabledSelect(state))
        self.add_item(_EntryApprovalSelect(state))
        self.add_item(_EntryModeSelect(state))

        back_btn = discord.ui.Button(
            label="← 戻る", style=discord.ButtonStyle.secondary, row=3
        )
        back_btn.callback = self._back
        self.add_item(back_btn)

        next_btn = discord.ui.Button(
            label="次へ ➜", style=discord.ButtonStyle.success, row=3
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

        cancel_btn = discord.ui.Button(
            label="❌ キャンセル", style=discord.ButtonStyle.danger, row=3
        )
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 1
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 3
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)
