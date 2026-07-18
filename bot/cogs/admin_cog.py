"""Guild settings command: /guild settings"""

from __future__ import annotations

import json
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.models.guild_settings import GuildSettings
from bot.services.errors import ServiceError, ValidationError
from bot.ui.portal_view import PortalView, build_portal_embed
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

    guild = app_commands.Group(name="guild", description="サーバー設定")
    guild_portal_grp = app_commands.Group(name="portal", description="句会ポータル", parent=guild)

    @guild.command(name="settings", description="句会作成権限の確認・更新を行います")
    @app_commands.describe(
        create_role="作成権限: everyone/admin/owner/role/specific",
        role_ids="create_role=role のときのロールID(カンマ区切り)",
        user_ids="create_role=specific のときのユーザーID(カンマ区切り)",
        participation_record_visibility="他人の参加記録公開: private/guild_public",
    )
    async def guild_settings(
        self,
        interaction: discord.Interaction,
        create_role: Literal["everyone", "admin", "owner", "role", "specific"] | None = None,
        role_ids: str | None = None,
        user_ids: str | None = None,
        participation_record_visibility: Literal["private", "guild_public"] | None = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                settings = await session.get(GuildSettings, interaction.guild.id)
                if settings is None:
                    settings = GuildSettings(guild_id=interaction.guild.id)
                    session.add(settings)
                    await session.flush()

                if create_role is None and participation_record_visibility is None:
                    embed = discord.Embed(title="Guild Settings", color=discord.Color.blue())
                    embed.add_field(name="create_role", value=settings.create_role, inline=False)
                    embed.add_field(name="role_ids", value=settings.create_role_ids, inline=False)
                    embed.add_field(name="user_ids", value=settings.create_user_ids, inline=False)
                    embed.add_field(
                        name="participation_record_visibility",
                        value=settings.participation_record_visibility,
                        inline=False,
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return

                if not _is_owner_or_admin(interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("設定変更はサーバー管理者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return

                if create_role is not None:
                    parsed_role_ids = _parse_id_csv(role_ids)
                    parsed_user_ids = _parse_id_csv(user_ids)
                    if create_role == "role" and not parsed_role_ids:
                        raise ValidationError("create_role=role の場合は role_ids を指定してください。")
                    if create_role == "specific" and not parsed_user_ids:
                        raise ValidationError("create_role=specific の場合は user_ids を指定してください。")

                    settings.create_role = create_role
                    settings.create_role_ids = json.dumps(parsed_role_ids, ensure_ascii=False)
                    settings.create_user_ids = json.dumps(parsed_user_ids, ensure_ascii=False)
                if participation_record_visibility is not None:
                    settings.participation_record_visibility = participation_record_visibility

            await interaction.response.send_message(
                embed=success_embed("Guild settings を更新しました。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @guild_portal_grp.command(name="setup", description="句会案内チャンネルを作成または再設定します")
    async def portal_setup(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if not _is_owner_or_admin(interaction.user):  # type: ignore[arg-type]
            await interaction.response.send_message(
                embed=error_embed("ポータル設定はサーバー管理者のみ実行できます。"),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            channel = None
            async with get_session() as session:
                settings = await session.get(GuildSettings, interaction.guild.id)
                if settings is None:
                    settings = GuildSettings(guild_id=interaction.guild.id)
                    session.add(settings)
                    await session.flush()

                if settings.portal_channel_id is not None:
                    existing = interaction.guild.get_channel(settings.portal_channel_id)
                    if isinstance(existing, discord.TextChannel):
                        channel = existing

                if channel is None:
                    channel = await interaction.guild.create_text_channel(
                        "句会案内",
                        reason="Kukai portal setup",
                    )
                    settings.portal_channel_id = channel.id

            await channel.send(embed=build_portal_embed(), view=PortalView())
            await interaction.edit_original_response(
                embed=success_embed(f"句会ポータルを設定しました。\nチャンネル: {channel.mention}")
            )
        except discord.Forbidden:
            await interaction.edit_original_response(
                embed=error_embed("チャンネル作成または投稿の権限がありません。")
            )
        except ServiceError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)))

    @guild_portal_grp.command(name="repost", description="句会案内チャンネルにポータルボタンを再投稿します")
    async def portal_repost(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if not _is_owner_or_admin(interaction.user):  # type: ignore[arg-type]
            await interaction.response.send_message(
                embed=error_embed("ポータル再投稿はサーバー管理者のみ実行できます。"),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            settings = await session.get(GuildSettings, interaction.guild.id)
            channel_id = settings.portal_channel_id if settings is not None else None
        if channel_id is None:
            await interaction.edit_original_response(
                embed=error_embed("ポータルチャンネルが未設定です。`/guild portal setup` を実行してください。")
            )
            return
        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.edit_original_response(
                embed=error_embed("保存済みポータルチャンネルが見つかりません。`/guild portal setup` を再実行してください。")
            )
            return
        try:
            await channel.send(embed=build_portal_embed(), view=PortalView())
        except discord.Forbidden:
            await interaction.edit_original_response(embed=error_embed("ポータルチャンネルへの投稿権限がありません。"))
            return
        await interaction.edit_original_response(
            embed=success_embed(f"ポータルボタンを再投稿しました。\nチャンネル: {channel.mention}")
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
