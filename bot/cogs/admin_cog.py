"""Admin commands: export/import/admin management + guild settings."""

from __future__ import annotations

import io
import json
from collections import Counter, defaultdict
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from bot.database import get_session
from bot.models.guild_settings import GuildSettings
from bot.models.select_rule import SelectLabel
from bot.repositories import entry_repo, select_repo, submission_repo
from bot.services import export_service, kukai_service, notification_service, permission_service
from bot.services.errors import ServiceError, ValidationError
from bot.utils.embed_builder import COLOR_INFO, error_embed, success_embed
from bot.utils.text import discord_safe


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


def _member_name(guild: discord.Guild, user_id: int, haigo: str | None = None) -> str:
    if haigo:
        return discord_safe(haigo)
    member = guild.get_member(user_id)
    return discord_safe(member.display_name if member else f"UID:{user_id}")


def _field_value(lines: list[str], *, limit: int = 1024) -> str:
    if not lines:
        return "（なし）"
    value = ""
    shown = 0
    for line in lines:
        candidate = f"{value}\n{line}" if value else line
        if len(candidate) > limit:
            remaining = len(lines) - shown
            suffix = f"\n…他 {remaining} 件"
            if value and len(value) + len(suffix) <= limit:
                value += suffix
            break
        value = candidate
        shown += 1
    return value or "（表示できる項目がありません）"


def _entry_status_icon(status: str) -> str:
    return {
        "approved": "✅",
        "pending": "⏳",
        "rejected": "❌",
        "withdrawn": "↩️",
    }.get(status, "•")


def _entry_status_label(status: str) -> str:
    return {
        "approved": "承認済",
        "pending": "審査待ち",
        "rejected": "却下",
        "withdrawn": "取消",
    }.get(status, status)


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

    @kukai.command(name="status", description="【管理者】エントリー・投句・選句の進捗を確認します")
    @app_commands.describe(kukai_id="句会ID")
    async def status(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この句会の管理者権限がありません。"),
                        ephemeral=True,
                    )
                    return

                entries = await entry_repo.list_by_kukai(session, kukai.id)
                submissions = await submission_repo.list_by_kukai(session, kukai.id)
                selects = await select_repo.get_all_selects(session, kukai.id)
                overall_comments = await select_repo.list_overall_comments(session, kukai.id)
                label_result = await session.execute(
                    select(SelectLabel)
                    .where(SelectLabel.kukai_id == kukai.id)
                    .order_by(SelectLabel.display_order)
                )
                labels = list(label_result.scalars().all())
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        guild = interaction.guild
        entry_by_user = {entry.user_id: entry for entry in entries}
        approved_entries = [entry for entry in entries if entry.status == "approved"]

        if kukai.entry_enabled:
            participant_user_ids = [entry.user_id for entry in approved_entries]
        else:
            participant_user_ids = sorted(
                {
                    row.user_id for row in submissions
                }
                | {row.selector_user_id for row in selects}
                | {row.user_id for row in overall_comments}
            )

        submission_counts = Counter(row.user_id for row in submissions)
        selects_by_user: dict[int, list] = defaultdict(list)
        for row in selects:
            selects_by_user[row.selector_user_id].append(row)
        overall_user_ids = {row.user_id for row in overall_comments}

        non_author_labels = [label for label in labels if label.label != "作者コメント"]

        embed = discord.Embed(
            title=f"管理者用 進捗確認 — {kukai.title}",
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"句会ID: {kukai.id} | 状態: {kukai.state}")

        if kukai.entry_enabled:
            entry_lines = [
                (
                    f"{_entry_status_icon(entry.status)} "
                    f"{_member_name(guild, entry.user_id, entry.haigo)} "
                    f"({_entry_status_label(entry.status)})"
                )
                for entry in entries
            ]
            embed.add_field(
                name=f"エントリー者 ({len(entries)}件 / 承認済 {len(approved_entries)}件)",
                value=_field_value(entry_lines),
                inline=False,
            )
        else:
            embed.add_field(
                name="エントリー者",
                value="エントリー制なし。投句・選句状況は記録済みユーザーを対象に表示します。",
                inline=False,
            )

        max_label = "∞" if kukai.submission_max is None else str(kukai.submission_max)
        submission_lines: list[str] = []
        for user_id in participant_user_ids:
            entry = entry_by_user.get(user_id)
            count = submission_counts.get(user_id, 0)
            icon = "✅" if count >= kukai.submission_min else "⚠️"
            submission_lines.append(
                f"{icon} {_member_name(guild, user_id, entry.haigo if entry else None)} "
                f"{count}句投句済（必要 {kukai.submission_min}〜{max_label}句）"
            )
        embed.add_field(
            name="投句状況",
            value=_field_value(submission_lines),
            inline=False,
        )

        selection_lines: list[str] = []
        for user_id in participant_user_ids:
            entry = entry_by_user.get(user_id)
            user_selects = selects_by_user.get(user_id, [])
            label_counts = Counter(
                row.select_label_id for row in user_selects if not row.is_self_comment
            )
            missing = [
                label
                for label in non_author_labels
                if label_counts.get(label.id, 0) < label.min_count
            ]
            icon = "✅" if not missing else "⚠️"
            parts = [
                f"{label.label}{label_counts.get(label.id, 0)}"
                for label in non_author_labels
            ]
            comment_count = sum(
                1 for row in user_selects if not row.is_self_comment and row.comment is not None
            )
            author_comment_count = sum(1 for row in user_selects if row.is_self_comment)
            parts.append(f"コメント{comment_count}")
            if author_comment_count:
                parts.append(f"作者コメント{author_comment_count}")
            parts.append(f"総評{1 if user_id in overall_user_ids else 0}")
            if missing:
                missing_text = " 不足:" + ",".join(
                    f"{label.label}{label_counts.get(label.id, 0)}/{label.min_count}"
                    for label in missing
                )
            else:
                missing_text = ""
            selection_lines.append(
                f"{icon} {_member_name(guild, user_id, entry.haigo if entry else None)} "
                f"{' '.join(parts)}{missing_text}"
            )

        if not non_author_labels:
            selection_lines = ["（作者コメント以外の選句ラベルがありません）"]
        embed.add_field(
            name="選句状況",
            value=_field_value(selection_lines),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

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
