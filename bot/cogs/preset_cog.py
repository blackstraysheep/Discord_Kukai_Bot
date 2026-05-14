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
from bot.services import permission_service, select_rule_service
from bot.services.errors import ServiceError
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
                templates = await select_rule_service.list_templates(session, interaction.guild.id)
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
            points_enabled, specs = select_rule_service.deserialize_template_payload(t.definition_json)
            label_strs = [f"{s['label']} ({s['point']:+d}pt)" for s in specs[:5]]
            default_mark = "（既定）" if bool(t.is_default) else ""
            pts_str = "点数あり" if points_enabled else "点数なし"
            embed.add_field(
                name=f"[{t.id}] {t.name}{default_mark}",
                value=f"{pts_str}  ラベル数: {len(specs)}\n" + (", ".join(label_strs) or "（未設定）"),
                inline=False,
            )
        if len(templates) > 10:
            embed.set_footer(text=f"他 {len(templates) - 10} 件")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
                template = await select_rule_service.create_or_update_template(
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
                template = await select_rule_service.rename_template(
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
                template = await select_rule_service.get_template(
                    session, interaction.guild.id, preset_id
                )
                name = template.name
                await select_rule_service.delete_template(session, interaction.guild.id, preset_id)
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
                template = await select_rule_service.set_template_points(
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
                templates = await select_rule_service.list_templates(session, interaction.guild.id)
                target = next((t for t in templates if t.id == preset_id), None)
                if target is None:
                    await interaction.response.send_message(
                        embed=error_embed("指定のプリセットが見つかりません。"), ephemeral=True
                    )
                    return
                for t in templates:
                    t.is_default = 1 if t.id == preset_id else 0
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
                template = await select_rule_service.get_template(
                    session, interaction.guild.id, preset_id
                )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        points_enabled, specs = select_rule_service.deserialize_template_payload(
            template.definition_json
        )
        pts_str = "点数あり" if points_enabled else "点数なし"
        embed = discord.Embed(
            title=f"プリセット「{template.name}」のラベル",
            description=f"ID: {template.id}  {pts_str}",
            color=COLOR_INFO,
        )
        if specs:
            lines = [f"**{s['label']}** {s['point']:+d}pt" for s in specs]
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
                template = await select_rule_service.add_or_update_label(
                    session,
                    guild_id=interaction.guild.id,
                    template_id=preset_id,
                    label=label_name,
                    point=point,
                )
                _, specs = select_rule_service.deserialize_template_payload(template.definition_json)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(
                f"プリセット「**{template.name}**」にラベル「**{label_name.strip()}**」を設定しました。\n"
                f"ラベル数: {len(specs)}"
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
                if new_name is not None:
                    template = await select_rule_service.rename_label(
                        session,
                        guild_id=interaction.guild.id,
                        template_id=preset_id,
                        old_label=label_name,
                        new_label=new_name,
                        point=point,
                    )
                else:
                    template = await select_rule_service.add_or_update_label(
                        session,
                        guild_id=interaction.guild.id,
                        template_id=preset_id,
                        label=label_name,
                        point=point,  # type: ignore[arg-type]
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
                template = await select_rule_service.remove_template_label(
                    session,
                    guild_id=interaction.guild.id,
                    template_id=preset_id,
                    label=label_name,
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
