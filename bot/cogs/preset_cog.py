"""Preset management commands: /preset *

Preset structure:
  - Preset: name + points_enabled
  - Labels: label-name + point (min/max/comment are set per-kukai in wizard step 5)
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import permission_service, preset_service
from bot.services.errors import ServiceError
from bot.ui.preset_wizard import open_preset_wizard
from bot.utils.bulk_parser import (
    BulkParseError,
    first_value,
    parse_bool,
    parse_fields,
    parse_label_spec,
    reject_unknown_keys,
    values_for,
)
from bot.utils.embed_builder import COLOR_INFO, error_embed, success_embed


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


class PresetCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    preset = app_commands.Group(name="preset", description="選句プリセットの管理")
    label = app_commands.Group(name="label", description="プリセット内のラベル管理", parent=preset)

    # ── Preset CRUD ──────────────────────────────────────────────────────────

    @preset.command(name="list", description="選句プリセット一覧を表示します")
    async def preset_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                templates = await preset_service.list_presets(session, interaction.guild.id)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        if not templates:
            await interaction.response.send_message(
                embed=discord.Embed(description="登録済みのプリセットはありません。", color=COLOR_INFO),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="選句プリセット一覧", color=COLOR_INFO)
        for t in templates[:10]:
            label_strs = [f"{s.label} ({s.point:+d}pt/rank{s.rank_priority})" for s in t.labels[:5]]
            default_mark = "（既定）" if t.is_default else ""
            pts_str = "点数あり" if t.points_enabled else "点数なし"
            embed.add_field(
                name=f"[{t.id}] {t.name}{default_mark}",
                value=f"{pts_str}  ラベル数: {len(t.labels)}\n" + (", ".join(label_strs) or "（未設定）"),
                inline=False,
            )
        if len(templates) > 10:
            embed.set_footer(text=f"他 {len(templates) - 10} 件")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @preset.command(name="gui", description="【管理者】選句プリセットGUIウィザードを開きます")
    async def preset_gui(self, interaction: discord.Interaction) -> None:
        if not await _check_admin(interaction):
            return
        await open_preset_wizard(interaction)

    @preset.command(name="bulk", description="【管理者】行形式で選句プリセットを一括登録・更新します")
    @app_commands.describe(config="name=... / label=名前,点数,rank,最小数,最大数,コメントモード")
    async def preset_bulk(self, interaction: discord.Interaction, config: str) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            fields = parse_fields(config)
            reject_unknown_keys(fields, {"name", "points_enabled", "set_default", "label"})
            name = first_value(fields, "name")
            if not name:
                raise BulkParseError("name は必須です。")
            points_enabled = parse_bool(
                first_value(fields, "points_enabled", "true") or "true",
                name="points_enabled",
            )
            set_default = parse_bool(
                first_value(fields, "set_default", "false") or "false",
                name="set_default",
            )
            labels = [
                parse_label_spec(field.value, line_no=field.line_no)
                for field in values_for(fields, "label")
            ]
        except BulkParseError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        try:
            async with get_session() as session:
                preset = await preset_service.create_preset(
                    session,
                    guild_id=interaction.guild.id,
                    created_by=interaction.user.id,
                    name=name,
                    points_enabled=points_enabled,
                    set_default=set_default,
                )
                if labels:
                    preset = await preset_service.replace_labels(
                        session,
                        guild_id=interaction.guild.id,
                        preset_id=preset.id,
                        labels=labels,
                    )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        label_lines = [
            f"{row.label} ({row.point:+d}pt / rank {row.rank_priority})"
            for row in preset.labels[:10]
        ]
        await interaction.response.send_message(
            embed=success_embed(
                f"プリセット「**{preset.name}**」を一括登録しました。\n"
                f"ID: `{preset.id}`  ラベル数: {len(preset.labels)}\n"
                + ("\n".join(label_lines) if label_lines else "ラベル未設定")
            ),
            ephemeral=True,
        )

    @preset.command(name="add", description="【管理者】新しいプリセットを追加します")
    @app_commands.describe(
        name="プリセット名",
        points_enabled="点数機能を有効にするか（デフォルト: True）",
        set_default="このプリセットを既定にする",
    )
    async def preset_add(
        self,
        interaction: discord.Interaction,
        name: str,
        points_enabled: bool = True,
        set_default: bool = False,
    ) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                template = await preset_service.create_preset(
                    session,
                    guild_id=interaction.guild.id,
                    created_by=interaction.user.id,
                    name=name,
                    points_enabled=points_enabled,
                    set_default=set_default,
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        pts = "有効" if points_enabled else "無効"
        await interaction.response.send_message(
            embed=success_embed(
                f"プリセット「**{template.name}**」を追加しました。\n"
                f"ID: `{template.id}`  点数: {pts}\n"
                "次に `/preset label add` でラベルを追加してください。"
            ),
            ephemeral=True,
        )

    @preset.command(name="rename", description="【管理者】プリセット名を変更します")
    @app_commands.describe(preset_id="プリセットID", new_name="新しい名前")
    async def preset_rename(
        self,
        interaction: discord.Interaction,
        preset_id: int,
        new_name: str,
    ) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                template = await preset_service.rename_preset(
                    session, interaction.guild.id, preset_id, new_name
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(f"プリセット `{preset_id}` を「**{template.name}**」に改名しました。"),
            ephemeral=True,
        )

    @preset.command(name="delete", description="【管理者】プリセットを削除します")
    @app_commands.describe(preset_id="プリセットID")
    async def preset_delete(
        self,
        interaction: discord.Interaction,
        preset_id: int,
    ) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                template = await preset_service.delete_preset(session, interaction.guild.id, preset_id)
                name = template.name
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(f"プリセット「**{name}**」（ID: {preset_id}）を削除しました。"),
            ephemeral=True,
        )

    @preset.command(name="set-points", description="【管理者】プリセットの点数機能を変更します")
    @app_commands.describe(
        preset_id="プリセットID",
        points_enabled="点数機能を有効にするか",
    )
    async def preset_set_points(
        self,
        interaction: discord.Interaction,
        preset_id: int,
        points_enabled: bool,
    ) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                template = await preset_service.set_preset_points(
                    session, interaction.guild.id, preset_id, points_enabled
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        pts = "有効" if points_enabled else "無効（全ラベルの点数を0に設定）"
        await interaction.response.send_message(
            embed=success_embed(
                f"プリセット「**{template.name}**」の点数機能を **{pts}** に変更しました。"
            ),
            ephemeral=True,
        )

    @preset.command(name="set-default", description="【管理者】プリセットを既定に設定します")
    @app_commands.describe(preset_id="プリセットID")
    async def preset_set_default(
        self,
        interaction: discord.Interaction,
        preset_id: int,
    ) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                target = await preset_service.set_default_preset(
                    session, interaction.guild.id, preset_id
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(f"プリセット「**{target.name}**」を既定に設定しました。"),
            ephemeral=True,
        )

    # ── Label management ─────────────────────────────────────────────────────

    @label.command(name="list", description="プリセットのラベル一覧を表示します")
    @app_commands.describe(preset_id="プリセットID")
    async def label_list(
        self,
        interaction: discord.Interaction,
        preset_id: int,
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                template = await preset_service.get_preset(
                    session, interaction.guild.id, preset_id
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        pts_str = "点数あり" if template.points_enabled else "点数なし"
        embed = discord.Embed(
            title=f"プリセット「{template.name}」のラベル",
            description=f"ID: {template.id}  {pts_str}",
            color=COLOR_INFO,
        )
        if template.labels:
            lines = [f"**{s.label}** {s.point:+d}pt / rank {s.rank_priority}" for s in template.labels]
            embed.add_field(name="ラベル", value="\n".join(lines), inline=False)
        else:
            embed.description = (embed.description or "") + "\n（ラベル未設定）"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @label.command(name="add", description="【管理者】プリセットにラベルを追加・更新します")
    @app_commands.describe(
        preset_id="プリセットID",
        label_name="ラベル名（例: 特選）",
        point="点数（点数なしプリセットは無視されます）",
    )
    async def label_add(
        self,
        interaction: discord.Interaction,
        preset_id: int,
        label_name: str,
        point: int = 0,
    ) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                template = await preset_service.upsert_label(
                    session,
                    guild_id=interaction.guild.id,
                    preset_id=preset_id,
                    label_name=label_name,
                    point=point,
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(
                f"プリセット「**{template.name}**」にラベル「**{label_name.strip()}**」を設定しました。\n"
                f"ラベル数: {len(template.labels)}"
            ),
            ephemeral=True,
        )

    @label.command(name="edit", description="【管理者】プリセットのラベル名・点数を変更します")
    @app_commands.describe(
        preset_id="プリセットID",
        label_name="変更対象のラベル名",
        new_name="新しいラベル名（省略で変更なし）",
        point="新しい点数（省略で変更なし）",
    )
    async def label_edit(
        self,
        interaction: discord.Interaction,
        preset_id: int,
        label_name: str,
        new_name: str | None = None,
        point: int | None = None,
    ) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        if new_name is None and point is None:
            await interaction.response.send_message(
                embed=error_embed("new_name または point のいずれかを指定してください。"),
                ephemeral=True,
            )
            return
        try:
            async with get_session() as session:
                template = await preset_service.edit_label(
                    session,
                    guild_id=interaction.guild.id,
                    preset_id=preset_id,
                    label_name=label_name,
                    new_name=new_name,
                    point=point,
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        result_label = new_name or label_name
        await interaction.response.send_message(
            embed=success_embed(
                f"プリセット「**{template.name}**」のラベル「**{result_label.strip()}**」を更新しました。"
            ),
            ephemeral=True,
        )

    @label.command(name="remove", description="【管理者】プリセットからラベルを削除します")
    @app_commands.describe(preset_id="プリセットID", label_name="削除するラベル名")
    async def label_remove(
        self,
        interaction: discord.Interaction,
        preset_id: int,
        label_name: str,
    ) -> None:
        if not await _check_admin(interaction):
            return
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                template = await preset_service.remove_label(
                    session,
                    guild_id=interaction.guild.id,
                    preset_id=preset_id,
                    label_name=label_name,
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(
                f"プリセット「**{template.name}**」からラベル「**{label_name.strip()}**」を削除しました。"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PresetCog(bot))
