"""Wizard step 4: Submission settings."""

from __future__ import annotations

import discord

from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard

_SUBMISSION_MODE_LABELS = {
    "manual": "手動（管理者が手動で次へ進める）",
    "semi_auto": "半自動（全員投句完了で自動進行）",
    "full_auto": "全自動（締切到達で自動進行）",
}


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title=f"ステップ 4/{STEP_COUNT}: 投句設定",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="進行モード",
        value=_SUBMISSION_MODE_LABELS.get(state.submission_mode, state.submission_mode),
        inline=False,
    )
    max_label = "∞" if state.submission_max is None else str(state.submission_max)
    embed.add_field(name="最低投句数", value=str(state.submission_min), inline=True)
    embed.add_field(name="最大投句数", value=max_label, inline=True)
    embed.set_footer(text="詳細設定で最低/最大投句数を変更できます。")
    return embed, StepSubmissionView(state)


class _SubmissionModeSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="投句進行モード",
            options=[
                discord.SelectOption(
                    label="手動",
                    description="管理者が手動で次のフェーズへ進める",
                    value="manual",
                    default=state.submission_mode == "manual",
                ),
                discord.SelectOption(
                    label="半自動",
                    description="全員が投句完了したら自動進行",
                    value="semi_auto",
                    default=state.submission_mode == "semi_auto",
                ),
                discord.SelectOption(
                    label="全自動",
                    description="締切到達で自動進行",
                    value="full_auto",
                    default=state.submission_mode == "full_auto",
                ),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state.submission_mode = self.values[0]
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class StepSubmissionView(discord.ui.View):
    def __init__(self, state: WizardState) -> None:
        super().__init__(timeout=900)
        self.state = state
        self.add_item(_SubmissionModeSelect(state))

        detail_btn = discord.ui.Button(
            label="🔢 投句数制限を設定",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        detail_btn.callback = self._detail
        self.add_item(detail_btn)

        back_btn = discord.ui.Button(
            label="← 戻る", style=discord.ButtonStyle.secondary, row=2
        )
        back_btn.callback = self._back
        self.add_item(back_btn)

        next_btn = discord.ui.Button(
            label="次へ ➜", style=discord.ButtonStyle.success, row=2
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

        cancel_btn = discord.ui.Button(
            label="❌ キャンセル", style=discord.ButtonStyle.danger, row=2
        )
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _detail(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SubmissionDetailModal(self.state))

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 3
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 5
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)


class SubmissionDetailModal(discord.ui.Modal, title="投句数制限の設定"):
    min_count = discord.ui.TextInput(
        label="最低投句数",
        placeholder="1",
        max_length=2,
        default="1",
    )
    max_count = discord.ui.TextInput(
        label="最大投句数（∞可）",
        placeholder="3 / ∞",
        required=False,
        max_length=8,
    )

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        self.min_count.default = str(state.submission_min)
        if state.submission_max is None:
            self.max_count.default = "∞"
        else:
            self.max_count.default = str(state.submission_max)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            mn = int(self.min_count.value)
        except ValueError:
            await interaction.response.send_message("数値を入力してください。", ephemeral=True)
            return

        raw_max_text = self.max_count.value.strip()
        max_text = raw_max_text.lower()
        if not raw_max_text:
            mx = None
        elif max_text in {"∞", "inf", "infinity", "unlimited", "無制限"}:
            mx = None
        else:
            try:
                mx = int(max_text)
            except ValueError:
                await interaction.response.send_message(
                    "最大投句数は数値または「∞」で指定してください。", ephemeral=True
                )
                return

        if mn < 1:
            await interaction.response.send_message(
                "最低投句数は1以上にしてください。", ephemeral=True
            )
            return
        if mx is not None and (mx < 1 or mn > mx):
            await interaction.response.send_message(
                "最大投句数は1以上、かつ最低投句数以上にしてください。", ephemeral=True
            )
            return
        self.state.submission_min = mn
        self.state.submission_max = mx
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)
