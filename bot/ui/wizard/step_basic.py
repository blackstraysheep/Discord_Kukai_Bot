"""Wizard step 1: Basic Info (title, theme, description)."""

from __future__ import annotations

import discord

from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    channel_ready = (not state.use_existing_channel) or (state.existing_channel_id is not None)
    filled = bool(state.title and channel_ready)
    embed = discord.Embed(
        title=f"ステップ 1/{STEP_COUNT}: 基本情報",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="題名", value=state.title or "（未入力）", inline=False)
    if state.theme:
        embed.add_field(name="題（お題）", value=state.theme, inline=True)
    if state.description:
        embed.add_field(name="説明", value=state.description[:200], inline=False)
    channel_mode = "既存チャンネルを使う" if state.use_existing_channel else "新規チャンネルを作成"
    channel_value = channel_mode
    if state.use_existing_channel:
        channel_value += (
            f"\n選択: <#{state.existing_channel_id}>"
            if state.existing_channel_id
            else "\n選択: （未選択）"
        )
    embed.add_field(name="句会チャンネル", value=channel_value, inline=False)
    embed.set_footer(text="✅ 題名とチャンネル設定がそろうと次へ進めます。")
    return embed, StepBasicView(state, filled=filled)


class StepBasicView(discord.ui.View):
    def __init__(self, state: WizardState, *, filled: bool) -> None:
        super().__init__(timeout=900)
        self.state = state

        fill_btn = discord.ui.Button(
            label="✏️ 基本情報を入力",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        fill_btn.callback = self._fill
        self.add_item(fill_btn)

        self.add_item(_ChannelModeSelect(state))
        self.add_item(_ExistingChannelSelect(state))

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
        await interaction.response.send_modal(StepBasicModal(self.state))

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 2
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)


class _ChannelModeSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="句会チャンネルの作成方法",
            options=[
                discord.SelectOption(
                    label="新規チャンネルを作成",
                    value="new",
                    default=not state.use_existing_channel,
                ),
                discord.SelectOption(
                    label="既存チャンネルを使用",
                    value="existing",
                    default=state.use_existing_channel,
                ),
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.state.use_existing_channel = self.values[0] == "existing"
        if not self.state.use_existing_channel:
            self.state.existing_channel_id = None
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class _ExistingChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        super().__init__(
            placeholder="既存チャンネルを選択",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            disabled=not state.use_existing_channel,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        self.state.existing_channel_id = int(channel.id)
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class StepBasicModal(discord.ui.Modal, title="基本情報の入力"):
    kukai_title = discord.ui.TextInput(
        label="題名 *",
        placeholder="第1回 春の句会",
        max_length=200,
    )
    theme = discord.ui.TextInput(
        label="題（お題）",
        placeholder="春（省略可）",
        required=False,
        max_length=100,
    )
    description = discord.ui.TextInput(
        label="説明",
        style=discord.TextStyle.paragraph,
        placeholder="句会の説明（省略可）",
        required=False,
        max_length=1000,
    )

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        if state.title:
            self.kukai_title.default = state.title
        if state.theme:
            self.theme.default = state.theme
        if state.description:
            self.description.default = state.description

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.state.title = self.kukai_title.value.strip()
        self.state.theme = self.theme.value.strip()
        self.state.description = self.description.value.strip()
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)
