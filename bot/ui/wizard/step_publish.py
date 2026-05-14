"""Wizard step 6: Progress mode + author reveal settings."""

from __future__ import annotations

import discord

from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard


def _sync_result_mode(state: WizardState) -> None:
    state.result_mode = "manual" if state.selecting_mode == "manual" else "auto"


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    _sync_result_mode(state)
    mode_ja = {"manual": "手動", "semi_auto": "半自動", "full_auto": "全自動"}
    submission_mode = mode_ja.get(state.submission_mode, state.submission_mode)
    selecting_mode = mode_ja.get(state.selecting_mode, state.selecting_mode)
    result_str = "手動" if state.result_mode == "manual" else "自動（選句進行に連動）"
    reveal_str = "公開" if state.author_reveal else "非公開"
    if state.author_reveal:
        zero_reveal_str = "公開" if state.author_reveal_zero else "非公開"
    else:
        zero_reveal_str = "（作者非公開時は適用外）"
    embed = discord.Embed(
        title=f"ステップ 6/{STEP_COUNT}: 公開・結果設定",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="投句進行モード", value=submission_mode, inline=True)
    embed.add_field(name="選句進行モード", value=selecting_mode, inline=True)
    embed.add_field(name="結果公開モード", value=result_str, inline=False)
    embed.add_field(name="作者公開", value=reveal_str, inline=True)
    embed.add_field(name="0点以下作者", value=zero_reveal_str, inline=True)
    return embed, StepPublishView(state)


class _SubmissionModeSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="投句進行モード",
            options=[
                discord.SelectOption(
                    label="手動（管理者が手動で次へ進める）",
                    value="manual",
                    default=state.submission_mode == "manual",
                ),
                discord.SelectOption(
                    label="半自動（全員投句完了で自動進行）",
                    value="semi_auto",
                    default=state.submission_mode == "semi_auto",
                ),
                discord.SelectOption(
                    label="全自動（締切到達で自動進行）",
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


class _SelectingModeSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="選句進行モード",
            options=[
                discord.SelectOption(
                    label="手動（管理者が手動で次へ進める）",
                    value="manual",
                    default=state.selecting_mode == "manual",
                ),
                discord.SelectOption(
                    label="半自動（全員選句完了で自動進行）",
                    value="semi_auto",
                    default=state.selecting_mode == "semi_auto",
                ),
                discord.SelectOption(
                    label="全自動（締切到達で自動進行）",
                    value="full_auto",
                    default=state.selecting_mode == "full_auto",
                ),
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state.selecting_mode = self.values[0]
        _sync_result_mode(self.state)
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class _AuthorRevealSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="作者公開設定",
            options=[
                discord.SelectOption(
                    label="公開（結果時に作者名を表示）",
                    value="true",
                    default=state.author_reveal,
                ),
                discord.SelectOption(
                    label="非公開（作者名を隠す）",
                    value="false",
                    default=not state.author_reveal,
                ),
            ],
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state.author_reveal = self.values[0] == "true"
        if not self.state.author_reveal:
            self.state.author_reveal_zero = True
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class _AuthorRevealZeroSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        if not state.author_reveal:
            options = [
                discord.SelectOption(
                    label="適用外（作者非公開）",
                    value="n/a",
                    default=True,
                )
            ]
        else:
            options = [
                discord.SelectOption(
                    label="公開する",
                    value="true",
                    default=state.author_reveal_zero,
                ),
                discord.SelectOption(
                    label="公開しない",
                    value="false",
                    default=not state.author_reveal_zero,
                ),
            ]
        super().__init__(
            placeholder="0点以下作者の公開",
            disabled=not state.author_reveal,
            options=options,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.state.author_reveal:
            await interaction.response.send_message(
                "作者非公開時は0点以下作者の設定は変更できません。", ephemeral=True
            )
            return
        self.state.author_reveal_zero = self.values[0] == "true"
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class StepPublishView(discord.ui.View):
    def __init__(self, state: WizardState) -> None:
        super().__init__(timeout=900)
        self.state = state
        self.add_item(_SubmissionModeSelect(state))
        self.add_item(_SelectingModeSelect(state))
        self.add_item(_AuthorRevealSelect(state))

        zero_label = (
            "0点以下作者: 公開"
            if state.author_reveal and state.author_reveal_zero
            else "0点以下作者: 非公開"
        )
        if not state.author_reveal:
            zero_label = "0点以下作者: 適用外"
        zero_btn = discord.ui.Button(
            label=zero_label,
            style=discord.ButtonStyle.primary,
            row=3,
            disabled=not state.author_reveal,
        )
        zero_btn.callback = self._toggle_author_reveal_zero
        self.add_item(zero_btn)

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

    async def _toggle_author_reveal_zero(self, interaction: discord.Interaction) -> None:
        if not self.state.author_reveal:
            await interaction.response.send_message(
                "作者非公開時は0点以下作者の設定は変更できません。", ephemeral=True
            )
            return
        self.state.author_reveal_zero = not self.state.author_reveal_zero
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 5
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 7
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)
