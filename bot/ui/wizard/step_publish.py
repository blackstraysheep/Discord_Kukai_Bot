"""Wizard step 6: Progress mode + author publication settings."""

from __future__ import annotations

import discord

from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard


AUTHOR_PUBLICATION_LABELS = {
    "with_result": "結果公開と同時に作者を公開",
    "manual": "結果公開後に作者を手動公開",
    "never": "作者公開はしない",
}


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    mode_ja = {"manual": "手動", "semi_auto": "半自動", "full_auto": "全自動"}
    submission_mode = mode_ja.get(state.submission_mode, state.submission_mode)
    selecting_mode = mode_ja.get(state.selecting_mode, state.selecting_mode)
    author_mode = AUTHOR_PUBLICATION_LABELS.get(
        state.author_publication_mode,
        state.author_publication_mode,
    )
    if state.author_publication_mode == "never":
        zero_reveal_str = "適用外"
    else:
        zero_reveal_str = "公開" if state.author_reveal_zero else "非公開"
    embed = discord.Embed(
        title=f"ステップ 6/{STEP_COUNT}: 公開・結果設定",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="投句進行モード", value=submission_mode, inline=True)
    embed.add_field(name="選句進行モード", value=selecting_mode, inline=True)
    embed.add_field(name="作者公開設定", value=author_mode, inline=False)
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
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class _AuthorPublicationModeSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="作者公開設定",
            options=[
                discord.SelectOption(
                    label="結果公開と同時に作者を公開",
                    value="with_result",
                    default=state.author_publication_mode == "with_result",
                ),
                discord.SelectOption(
                    label="結果公開後に作者を手動公開",
                    value="manual",
                    default=state.author_publication_mode == "manual",
                ),
                discord.SelectOption(
                    label="作者公開はしない",
                    value="never",
                    default=state.author_publication_mode == "never",
                ),
            ],
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state.author_publication_mode = self.values[0]
        self.state.author_reveal = self.state.author_publication_mode == "with_result"
        if self.state.author_publication_mode == "never":
            self.state.author_reveal_zero = True
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class _AuthorRevealZeroSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        if state.author_publication_mode == "never":
            options = [
                discord.SelectOption(
                    label="適用外（作者公開なし）",
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
            disabled=state.author_publication_mode == "never",
            options=options,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.state.author_publication_mode == "never":
            await interaction.response.send_message(
                "作者公開なしの場合は0点以下作者の設定は変更できません。", ephemeral=True
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
        self.add_item(_AuthorPublicationModeSelect(state))
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
        self.state.step = 5
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 7
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)
