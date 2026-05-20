"""Notification preset management commands: /notify-preset *"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import notification_preset_service, permission_service
from bot.services.errors import ServiceError
from bot.utils.bulk_parser import (
    BulkParseError,
    first_value,
    parse_bool,
    parse_fields,
    parse_reminder_spec,
    reject_unknown_keys,
    values_for,
)
from bot.utils.embed_builder import COLOR_INFO, error_embed, success_embed

_EVENT_LABELS = {
    "entry_close": "エントリー",
    "submission_close": "投句",
    "selecting_close": "選句",
    "voice_start": "ボイス",
}


def _format_entries(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        event = _EVENT_LABELS.get(str(e.get("event_type", "")), str(e.get("event_type", "")))
        offset_h = int(e.get("offset_secs", 0)) // 3600
        channel_id = e.get("channel_id")
        mention = bool(e.get("mention"))
        if channel_id == -1:
            dest = "DM"
        elif channel_id is None:
            dest = "句会チャンネル"
        else:
            dest = f"<#{channel_id}>"
        if mention:
            dest += " + mention"
        target = e.get("target", "all")
        lines.append(f"{event}: {offset_h}h前 / {dest} / {target}")
    return "\n".join(lines) if lines else "（エントリーなし）"


async def _check_admin(interaction: discord.Interaction) -> bool:
    assert interaction.guild is not None
    async with get_session() as session:
        allowed = await permission_service.can_create_kukai(
            session, interaction.guild.id, interaction.user  # type: ignore[arg-type]
        )
    if not allowed:
        await interaction.response.send_message(
            embed=error_embed("この操作を行う権限がありません。"), ephemeral=True
        )
    return allowed


class NotifyPresetCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    notify_preset = app_commands.Group(name="notify-preset", description="通知プリセットの管理")

    @notify_preset.command(name="list", description="【作成権限者】通知プリセット一覧を表示します")
    async def preset_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                presets = await notification_preset_service.list_presets(
                    session, interaction.guild.id
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        if not presets:
            await interaction.response.send_message(
                embed=discord.Embed(description="登録済みの通知プリセットはありません。", color=COLOR_INFO),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="通知プリセット一覧", color=COLOR_INFO)
        for p in presets[:10]:
            entries = notification_preset_service.entries_from_json(p.entries_json)
            default_mark = "（既定）" if p.is_default else ""
            embed.add_field(
                name=f"[{p.id}] {p.name}{default_mark}",
                value=_format_entries(entries[:5]),
                inline=False,
            )
        if len(presets) > 10:
            embed.set_footer(text=f"他 {len(presets) - 10} 件")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @notify_preset.command(name="add", description="【作成権限者】通知プリセットを新規作成します")
    async def preset_add(self, interaction: discord.Interaction) -> None:
        if not await _check_admin(interaction):
            return
        await interaction.response.send_modal(_AddPresetModal())

    @notify_preset.command(name="bulk", description="【作成権限者】行形式で通知プリセットを一括作成・更新します")
    @app_commands.describe(config="name=..., set_default=true/false, entry=event,offset,dest,target,mention")
    async def preset_bulk(self, interaction: discord.Interaction, config: str) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            fields = parse_fields(config)
            reject_unknown_keys(fields, {"name", "set_default", "entry"})
            name = first_value(fields, "name")
            if not name:
                raise BulkParseError("name= を指定してください。")
            set_default = parse_bool(first_value(fields, "set_default") or "false", name="set_default")
            entry_fields = values_for(fields, "entry")
            if not entry_fields:
                raise BulkParseError("entry= を1件以上指定してください。")
            entries = [
                parse_reminder_spec(f.value, line_no=f.line_no) for f in entry_fields
            ]
        except BulkParseError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        try:
            async with get_session() as session:
                preset = await notification_preset_service.create_preset(
                    session,
                    guild_id=interaction.guild.id,
                    created_by=interaction.user.id,
                    name=name,
                    entries=entries,
                    set_default=set_default,
                )
            default_str = "（既定に設定）" if set_default else ""
            await interaction.response.send_message(
                embed=success_embed(f"プリセット「{preset.name}」を保存しました。{default_str}"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @notify_preset.command(name="delete", description="【作成権限者】通知プリセットを削除します")
    @app_commands.describe(name="削除するプリセット名")
    async def preset_delete(self, interaction: discord.Interaction, name: str) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                await notification_preset_service.delete_preset(
                    session, interaction.guild.id, name
                )
            await interaction.response.send_message(
                embed=success_embed(f"プリセット「{name}」を削除しました。"), ephemeral=True
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @notify_preset.command(name="set-default", description="【作成権限者】ギルドの既定通知プリセットを設定します")
    @app_commands.describe(name="既定にするプリセット名")
    async def preset_set_default(self, interaction: discord.Interaction, name: str) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                await notification_preset_service.set_default_preset(
                    session, interaction.guild.id, name
                )
            await interaction.response.send_message(
                embed=success_embed(f"「{name}」を既定の通知プリセットに設定しました。"), ephemeral=True
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)


class _AddPresetModal(discord.ui.Modal, title="通知プリセット作成"):
    name_input = discord.ui.TextInput(
        label="プリセット名",
        placeholder="例: 標準",
        max_length=100,
        required=True,
    )
    entries_input = discord.ui.TextInput(
        label="通知（1行1件）書式: event,offset,dest,target,mention",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "submission_close,24h,kukai,all,false\n"
            "selecting_close,24h,kukai,all,false"
        ),
        required=True,
        max_length=2000,
    )
    set_default_input = discord.ui.TextInput(
        label="既定に設定 (true/false)",
        placeholder="false",
        default="false",
        max_length=5,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        name = self.name_input.value.strip()
        try:
            set_default = parse_bool(
                self.set_default_input.value.strip() or "false", name="既定に設定"
            )
            entries: list[dict] = []
            for line_no, raw in enumerate(self.entries_input.value.splitlines(), start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                entries.append(parse_reminder_spec(line, line_no=line_no))
            if not entries:
                await interaction.response.send_message(
                    embed=error_embed("通知エントリーを1件以上入力してください。"), ephemeral=True
                )
                return
        except BulkParseError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        try:
            async with get_session() as session:
                preset = await notification_preset_service.create_preset(
                    session,
                    guild_id=interaction.guild.id,
                    created_by=interaction.user.id,
                    name=name,
                    entries=entries,
                    set_default=set_default,
                )
            default_str = "（既定に設定）" if set_default else ""
            await interaction.response.send_message(
                embed=success_embed(f"プリセット「{preset.name}」を作成しました。{default_str}"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NotifyPresetCog(bot))
