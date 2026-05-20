"""Wizard step 8: notification reminder customization."""

from __future__ import annotations

import discord

from bot.repositories import notification_preset_repo
from bot.services import notification_preset_service
from bot.database import get_session
from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard
from bot.utils.bulk_parser import BulkParseError, parse_reminder_spec


_EVENT_LABELS = {
    "entry_close": "エントリー",
    "submission_close": "投句",
    "selecting_close": "選句",
    "voice_start": "ボイス",
}


def _format_destination(channel_id: int | None, mention: bool) -> str:
    if channel_id == -1:
        return "DM"
    base = "句会チャンネル" if channel_id is None else f"<#{channel_id}>"
    if mention:
        base += " + mention"
    return base


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title=f"ステップ 8/{STEP_COUNT}: 通知設定",
        color=discord.Color.blurple(),
    )
    if state.notification_specs:
        lines = []
        for row in state.notification_specs[:12]:
            lines.append(
                f"{_EVENT_LABELS.get(str(row['event_type']), row['event_type'])}: "
                f"{int(row['offset_secs']) // 3600}h前 / "
                f"{_format_destination(row.get('channel_id'), bool(row.get('mention')))} / "
                f"{row.get('target', 'all')}"
            )
        embed.add_field(name="カスタム通知", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="通知", value="デフォルト（投句・選句の24時間前に句会チャンネル）", inline=False)
    embed.set_footer(text="書式: event,offset,destination,target,mention")
    return embed, StepNotifyView(state)


class _PresetSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        options = [discord.SelectOption(label="（手動入力）", value="__manual__")]
        for row in state.notify_preset_options[:24]:
            options.append(
                discord.SelectOption(label=str(row["name"]), value=str(row["id"]))
            )
        super().__init__(placeholder="プリセットから読み込む", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        if value == "__manual__":
            await interaction.response.defer()
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                preset = await notification_preset_repo.get_by_guild(session, interaction.guild.id)
                target = next((p for p in preset if str(p.id) == value), None)
                if target is None:
                    await interaction.response.send_message("プリセットが見つかりません。", ephemeral=True)
                    return
                entries = notification_preset_service.entries_from_json(target.entries_json)
        except Exception:
            await interaction.response.send_message("プリセットの読み込みに失敗しました。", ephemeral=True)
            return
        self.state.notification_specs = entries
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class StepNotifyView(discord.ui.View):
    def __init__(self, state: WizardState) -> None:
        super().__init__(timeout=900)
        self.state = state

        if state.notify_preset_options:
            self.add_item(_PresetSelect(state))

        edit_btn = discord.ui.Button(label="通知を入力", style=discord.ButtonStyle.primary, row=1)
        edit_btn.callback = self._edit
        self.add_item(edit_btn)

        clear_btn = discord.ui.Button(
            label="デフォルトに戻す",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=not state.notification_specs,
        )
        clear_btn.callback = self._clear
        self.add_item(clear_btn)

        back_btn = discord.ui.Button(label="← 戻る", style=discord.ButtonStyle.secondary, row=4)
        back_btn.callback = self._back
        self.add_item(back_btn)

        next_btn = discord.ui.Button(label="次へ ➜", style=discord.ButtonStyle.success, row=4)
        next_btn.callback = self._next
        self.add_item(next_btn)

        cancel_btn = discord.ui.Button(label="❌ キャンセル", style=discord.ButtonStyle.danger, row=4)
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _edit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NotificationModal(self.state))

    async def _clear(self, interaction: discord.Interaction) -> None:
        self.state.notification_specs = []
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 7
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 9
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)


class NotificationModal(discord.ui.Modal, title="通知設定"):
    reminders = discord.ui.TextInput(
        label="通知（1行1件）書式: event,offset,dest,target,mention",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "submission_close,24h,kukai,all\n"
            "selecting_close,1h,mention,incomplete\n"
            "voice_start,30m,dm,all"
        ),
        required=False,
        max_length=3000,
    )

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        if state.notification_specs:
            lines = []
            for row in state.notification_specs:
                destination = "dm" if row.get("channel_id") == -1 else (
                    "kukai" if row.get("channel_id") is None else str(row.get("channel_id"))
                )
                lines.append(
                    f"{row['event_type']},{row['offset_secs']}s,{destination},"
                    f"{row.get('target', 'all')},{str(bool(row.get('mention'))).lower()}"
                )
            self.reminders.default = "\n".join(lines)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        specs = []
        for line_no, raw in enumerate(self.reminders.value.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                specs.append(parse_reminder_spec(line, line_no=line_no))
            except BulkParseError as e:
                if not interaction.response.is_done():
                    await interaction.response.send_message(str(e), ephemeral=True)
                return
        self.state.notification_specs = specs
        set_wizard(self.state)
        embed, view = build(self.state)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=embed, view=view)
        except discord.HTTPException as exc:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"通知設定を保存しましたが表示の更新に失敗しました: {exc}", ephemeral=True
                )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "通知設定の処理中にエラーが発生しました。もう一度お試しください。", ephemeral=True
            )
