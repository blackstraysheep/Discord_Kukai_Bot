"""Wizard step 5: Publish / result settings."""

from __future__ import annotations

import discord

from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    publish_str = "手動" if state.publish_mode == "manual" else "自動（投句締切後に自動公開）"
    result_str = "手動" if state.result_mode == "manual" else "自動（選句締切後に自動集計）"
    reveal_str = "公開" if state.author_reveal else "非公開"
    if state.author_reveal:
        zero_reveal_str = "公開" if state.author_reveal_zero else "非公開"
    else:
        zero_reveal_str = "（作者非公開時は適用外）"
    embed = discord.Embed(
        title=f"ステップ 5/{STEP_COUNT}: 公開・結果設定",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="投句公開", value=publish_str, inline=True)
    embed.add_field(name="結果集計", value=result_str, inline=True)
    embed.add_field(name="作者公開", value=reveal_str, inline=True)
    embed.add_field(name="0点以下作者", value=zero_reveal_str, inline=True)
    return embed, StepPublishView(state)


class _PublishModeSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="投句公開モード",
            options=[
                discord.SelectOption(
                    label="手動（管理者が /kukai publish で公開）",
                    value="manual",
                    default=state.publish_mode == "manual",
                ),
                discord.SelectOption(
                    label="自動（投句締切後に自動公開）",
                    value="auto",
                    default=state.publish_mode == "auto",
                ),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state.publish_mode = self.values[0]
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class _ResultModeSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="結果集計モード",
            options=[
                discord.SelectOption(
                    label="手動（管理者が /kukai proceed で進める）",
                    value="manual",
                    default=state.result_mode == "manual",
                ),
                discord.SelectOption(
                    label="自動（選句締切後に自動集計）",
                    value="auto",
                    default=state.result_mode == "auto",
                ),
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state.result_mode = self.values[0]
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
        super().__init__(
            placeholder="0点以下作者の公開",
            disabled=not state.author_reveal,
            options=[
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
            ],
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
        self.add_item(_PublishModeSelect(state))
        self.add_item(_ResultModeSelect(state))
        self.add_item(_AuthorRevealSelect(state))
        self.add_item(_AuthorRevealZeroSelect(state))

        back_btn = discord.ui.Button(
            label="← 戻る", style=discord.ButtonStyle.secondary, row=4
        )
        back_btn.callback = self._back
        self.add_item(back_btn)

        next_btn = discord.ui.Button(
            label="次へ ➜", style=discord.ButtonStyle.success, row=4
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

        cancel_btn = discord.ui.Button(
            label="❌ キャンセル", style=discord.ButtonStyle.danger, row=4
        )
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 4
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 6
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)
