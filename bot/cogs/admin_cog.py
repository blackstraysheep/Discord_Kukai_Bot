"""Admin commands: export/import/admin management + guild settings."""

from __future__ import annotations

import io
import json
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.models.guild_settings import GuildSettings
from bot.services import export_service, kukai_service, notification_service, permission_service
from bot.services.errors import ServiceError, ValidationError
from bot.utils.embed_builder import error_embed, success_embed


def _is_owner_or_admin(member: discord.Member) -> bool:
    return member.id == member.guild.owner_id or member.guild_permissions.administrator


def _parse_id_csv(raw: str | None) -> list[int]:
    if raw is None:
        return []
    text = raw.strip()
    if not text:
        return []
    ids: list[int] = []
    for token in text.split(","):
        value = token.strip()
        if not value:
            continue
        if not value.isdigit():
            raise ValidationError("ID一覧はカンマ区切りの数値で指定してください。")
        ids.append(int(value))
    return ids


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    kukai = app_commands.Group(name="kukai_admin", description="句会管理者・エクスポート操作")
    guild = app_commands.Group(name="guild", description="サーバー設定")

    @kukai.command(name="export", description="【管理者】句会データをエクスポートします")
    @app_commands.describe(
        kukai_id="句会ID（省略で全句会）",
        export_format="出力形式",
    )
    async def export(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
        export_format: Literal["json", "csv"] = "json",
    ) -> None:
        assert interaction.guild is not None

        try:
            async with get_session() as session:
                if kukai_id is None:
                    if not _is_owner_or_admin(interaction.user):  # type: ignore[arg-type]
                        await interaction.response.send_message(
                            embed=error_embed("全句会エクスポートはサーバー管理者のみ実行できます。"),
                            ephemeral=True,
                        )
                        return
                else:
                    kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                    if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                        await interaction.response.send_message(
                            embed=error_embed("この句会の管理者権限がありません。"),
                            ephemeral=True,
                        )
                        return

                payload = await export_service.export_payload(
                    session,
                    guild_id=interaction.guild.id,
                    kukai_id=kukai_id,
                )

            if export_format == "csv":
                content = export_service.payload_to_csv(payload).encode("utf-8")
                filename = "kukai_export.csv"
            else:
                content = export_service.payload_to_json(payload).encode("utf-8")
                filename = "kukai_export.json"

            await interaction.user.send(
                content=f"句会データを送付します（{export_format.upper()}）",
                file=discord.File(io.BytesIO(content), filename=filename),
            )
            await interaction.response.send_message(
                embed=success_embed("DMにエクスポートファイルを送付しました。"),
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("DMを送信できませんでした。DM受信設定を確認してください。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="import_data", description="【管理者】句会データ(JSON)をインポートします")
    @app_commands.describe(file="exportで出力したJSONファイル")
    async def import_data(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
    ) -> None:
        assert interaction.guild is not None

        if not _is_owner_or_admin(interaction.user):  # type: ignore[arg-type]
            await interaction.response.send_message(
                embed=error_embed("インポートはサーバー管理者のみ実行できます。"),
                ephemeral=True,
            )
            return

        try:
            raw = (await file.read()).decode("utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValidationError("JSON形式が不正です。")

            async with get_session() as session:
                imported_ids = await export_service.import_payload(
                    session,
                    guild_id=interaction.guild.id,
                    payload=payload,
                )
                for imported_id in imported_ids:
                    kukai = await kukai_service.get_kukai(session, imported_id, interaction.guild.id)
                    await notification_service.schedule_kukai_jobs(session, kukai)

            await interaction.response.send_message(
                embed=success_embed(
                    f"{len(imported_ids)}件の句会をインポートしました。\n"
                    f"句会ID: {', '.join(str(kid) for kid in imported_ids)}"
                ),
                ephemeral=True,
            )
        except UnicodeDecodeError:
            await interaction.response.send_message(
                embed=error_embed("UTF-8のJSONファイルを指定してください。"),
                ephemeral=True,
            )
        except json.JSONDecodeError:
            await interaction.response.send_message(
                embed=error_embed("JSONのパースに失敗しました。ファイル内容を確認してください。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="add_admin", description="【管理者】句会管理者を追加します")
    @app_commands.describe(kukai_id="句会ID", user="追加するユーザー")
    async def add_admin(
        self, interaction: discord.Interaction, kukai_id: int, user: discord.Member
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                allowed = (
                    interaction.user.id == interaction.guild.owner_id
                    or await permission_service.is_kukai_admin(session, kukai, interaction.user)  # type: ignore[arg-type]
                )
                if not allowed:
                    await interaction.response.send_message(
                        embed=error_embed("管理者追加は句会管理者またはサーバー所有者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return
                await kukai_service.add_kukai_admin(
                    session,
                    kukai,
                    user_id=user.id,
                    added_by=interaction.user.id,
                )
            await interaction.response.send_message(
                embed=success_embed(f"{user.mention} を句会管理者に追加しました。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="remove_admin", description="【管理者】句会管理者を削除します")
    @app_commands.describe(kukai_id="句会ID", user="削除するユーザー")
    async def remove_admin(
        self, interaction: discord.Interaction, kukai_id: int, user: discord.Member
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                allowed = (
                    interaction.user.id == interaction.guild.owner_id
                    or interaction.user.id == kukai.created_by
                )
                if not allowed:
                    await interaction.response.send_message(
                        embed=error_embed("管理者削除は句会作成者またはサーバー所有者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return

                await kukai_service.remove_kukai_admin(session, kukai, user_id=user.id)

            await interaction.response.send_message(
                embed=success_embed(f"{user.mention} を句会管理者から削除しました。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @guild.command(name="settings", description="句会作成権限の確認・更新を行います")
    @app_commands.describe(
        create_role="作成権限: everyone/admin/owner/role/specific",
        role_ids="create_role=role のときのロールID(カンマ区切り)",
        user_ids="create_role=specific のときのユーザーID(カンマ区切り)",
    )
    async def guild_settings(
        self,
        interaction: discord.Interaction,
        create_role: Literal["everyone", "admin", "owner", "role", "specific"] | None = None,
        role_ids: str | None = None,
        user_ids: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                settings = await session.get(GuildSettings, interaction.guild.id)
                if settings is None:
                    settings = GuildSettings(guild_id=interaction.guild.id)
                    session.add(settings)
                    await session.flush()

                if create_role is None:
                    embed = discord.Embed(title="Guild Settings", color=discord.Color.blue())
                    embed.add_field(name="create_role", value=settings.create_role, inline=False)
                    embed.add_field(name="role_ids", value=settings.create_role_ids, inline=False)
                    embed.add_field(name="user_ids", value=settings.create_user_ids, inline=False)
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return

                if not _is_owner_or_admin(interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("設定変更はサーバー管理者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return

                parsed_role_ids = _parse_id_csv(role_ids)
                parsed_user_ids = _parse_id_csv(user_ids)
                if create_role == "role" and not parsed_role_ids:
                    raise ValidationError("create_role=role の場合は role_ids を指定してください。")
                if create_role == "specific" and not parsed_user_ids:
                    raise ValidationError("create_role=specific の場合は user_ids を指定してください。")

                settings.create_role = create_role
                settings.create_role_ids = json.dumps(parsed_role_ids, ensure_ascii=False)
                settings.create_user_ids = json.dumps(parsed_user_ids, ensure_ascii=False)

            await interaction.response.send_message(
                embed=success_embed("Guild settings を更新しました。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
