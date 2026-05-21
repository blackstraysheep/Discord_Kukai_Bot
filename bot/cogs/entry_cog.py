"""Entry commands: /entry *

Note: Discord requires all subcommands under the same group namespace.
The original requirements listed '/entry' (join) without a subcommand, but
Discord does not allow a group and a same-name leaf command to coexist.
We use '/entry join' for consistency.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from bot.database import get_session
from bot.models.kukai import KukaiAdmin
from bot.services import entry_service, kukai_service, permission_service
from bot.services.errors import ServiceError
from bot.ui.entry_manage_view import EntryManageView
from bot.utils.channel import effective_channel_id
from bot.utils.discord_retry import send_with_retry
from bot.utils.embed_builder import (
    COLOR_INFO,
    error_embed,
    success_embed,
)
from bot.utils.entry_notifications import notify_entry_approved
from bot.utils.entry_notifications import notify_entries_approved, notify_entry_rejected

logger = logging.getLogger(__name__)


class EntryHaigoModal(discord.ui.Modal, title="エントリー"):
    haigo = discord.ui.TextInput(
        label="俳号（ペンネーム）",
        placeholder="空欄の場合はサーバーの表示名を使用します",
        required=False,
        max_length=100,
    )

    def __init__(self, kukai_id: int | None, channel_id: int | None, guild_id: int) -> None:
        super().__init__()
        self.kukai_id = kukai_id
        self.channel_id = channel_id
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None

        haigo = self.haigo.value.strip() or None
        try:
            late_entry = False
            admin_ids: list[int] = []
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=self.guild_id,
                    channel_id=self.channel_id,
                    kukai_id=self.kukai_id,
                )
                late_entry = entry_service.is_late_entry_request(kukai)
                entry = await entry_service.enter(
                    session, kukai, interaction.user.id, haigo
                )
                if late_entry and entry.status == "pending":
                    result = await session.execute(
                        select(KukaiAdmin.user_id).where(KukaiAdmin.kukai_id == kukai.id)
                    )
                    admin_ids = [kukai.created_by, *result.scalars().all()]
            display_name = entry.haigo or interaction.user.display_name  # type: ignore[union-attr]
            if entry.status == "approved":
                msg = f"「**{kukai.title}**」にエントリーしました。\n俳号: **{display_name}**"
            else:
                msg = (
                    f"「**{kukai.title}**」にエントリーしました（承認待ち）。\n"
                    f"俳号: **{display_name}**"
                )
            await interaction.followup.send(
                embed=success_embed(msg, title="エントリー完了"), ephemeral=True
            )
            if late_entry and entry.status == "pending":
                await _notify_late_entry_request(
                    interaction,
                    kukai_title=kukai.title,
                    kukai_id=kukai.id,
                    channel_id=kukai.channel_id,
                    admin_ids=admin_ids,
                    display_name=display_name,
                )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


class EntryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    entry = app_commands.Group(name="entry", description="句会へのエントリー操作")

    # ------------------------------------------------------------------
    # Participant commands
    # ------------------------------------------------------------------

    @entry.command(name="join", description="句会にエントリーします")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def entry_join(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        channel_id = effective_channel_id(interaction)
        await interaction.response.send_modal(
            EntryHaigoModal(
                kukai_id=kukai_id,
                channel_id=channel_id,
                guild_id=interaction.guild.id,
            )
        )

    @entry.command(name="cancel", description="エントリーを取り消します（受付期間中のみ）")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def entry_cancel(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                entry = await entry_service.withdraw(session, kukai, interaction.user.id)
            await interaction.followup.send(
                embed=success_embed(f"「{kukai.title}」のエントリーを取り消しました。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------

    @entry.command(name="list", description="【句会管理者】エントリー一覧を表示します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）", status="絞り込み (省略で全件)")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="審査待ち", value="pending"),
            app_commands.Choice(name="承認済み", value="approved"),
            app_commands.Choice(name="却下", value="rejected"),
            app_commands.Choice(name="取消", value="withdrawn"),
        ]
    )
    async def entry_list(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
        status: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.followup.send(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                entries = await entry_service.list_entries(session, kukai.id, status)

            if not entries:
                label = f"（{status}）" if status else ""
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"エントリー{label}はありません。", color=COLOR_INFO
                    ),
                    ephemeral=True,
                )
                return

            STATUS_JA = {"pending": "審査待", "approved": "承認済", "rejected": "却下", "withdrawn": "取消"}
            lines = []
            for e in entries:
                member = interaction.guild.get_member(e.user_id)
                name = e.haigo or (member.display_name if member else f"UID:{e.user_id}")
                tag = STATUS_JA.get(e.status, e.status)
                lines.append(f"[{tag}] **{name}** (`{e.user_id}`)")

            embed = discord.Embed(
                title=f"📋 エントリー一覧 — {kukai.title}",
                description="\n".join(lines[:40]),
                color=COLOR_INFO,
            )
            if len(entries) > 40:
                embed.set_footer(text=f"他 {len(entries) - 40} 件")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)

    @entry.command(name="approve", description="【句会管理者】エントリーを承認します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）", user="承認するユーザー（省略でリストから選択）")
    async def entry_approve(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        kukai_id: int | None = None,
    ) -> None:
        await self._admin_action(interaction, effective_channel_id(interaction), kukai_id, user, "approve")

    @entry.command(name="approve-all", description="【句会管理者】審査待ちエントリーを一括承認します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def entry_approve_all(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            approved_names: list[str] = []
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.followup.send(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                entries = await entry_service.list_entries(session, kukai.id, status="pending")
                for entry in entries:
                    approved = await entry_service.approve(
                        session,
                        kukai,
                        interaction.user.id,
                        entry.user_id,
                    )
                    member = interaction.guild.get_member(approved.user_id)
                    approved_names.append(
                        approved.haigo or (member.display_name if member else f"UID:{approved.user_id}")
                    )

            if not approved_names:
                await interaction.followup.send(
                    embed=discord.Embed(description="審査待ちのエントリーはありません。", color=COLOR_INFO),
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                embed=success_embed(f"{len(approved_names)}件のエントリーを承認しました。"),
                ephemeral=True,
            )
            await notify_entries_approved(interaction.guild, kukai, display_names=approved_names)
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)

    @entry.command(name="reject", description="【句会管理者】エントリーを却下します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）", user="却下するユーザー（省略でリストから選択）")
    async def entry_reject(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        kukai_id: int | None = None,
    ) -> None:
        await self._admin_action(interaction, effective_channel_id(interaction), kukai_id, user, "reject")

    @entry.command(name="remove", description="【句会管理者】エントリーを削除します（エントリー締切後）")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）", user="削除するユーザー")
    async def entry_remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        kukai_id: int | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.followup.send(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                await entry_service.admin_remove(session, kukai, user.id)
            await interaction.followup.send(
                embed=success_embed(f"{user.display_name} さんのエントリーを削除しました。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    async def _admin_action(
        self,
        interaction: discord.Interaction,
        channel_id: int | None,
        kukai_id: int | None,
        user: discord.Member | None,
        action: str,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        action_ja = "承認" if action == "approve" else "却下"
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=channel_id,
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.followup.send(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return

                if user is not None:
                    if action == "approve":
                        entry = await entry_service.approve(
                            session, kukai, interaction.user.id, user.id
                        )
                    else:
                        entry = await entry_service.reject(
                            session, kukai, interaction.user.id, user.id
                        )
                    name = entry.haigo or user.display_name
                    await interaction.followup.send(
                        embed=success_embed(f"**{name}** さんを{action_ja}しました。"),
                        ephemeral=True,
                    )
                    if action == "approve":
                        await notify_entry_approved(
                            interaction.guild,
                            kukai,
                            user_id=user.id,
                            display_name=name,
                        )
                    else:
                        await notify_entry_rejected(
                            interaction.guild,
                            kukai,
                            display_name=name,
                        )
                    return

                entries = await entry_service.list_entries(session, kukai.id, status="pending")

            if not entries:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description="審査待ちのエントリーはありません。",
                        color=COLOR_INFO,
                    ),
                    ephemeral=True,
                )
                return

            view = EntryManageView(kukai.id, entries, interaction.guild, action)
            await interaction.followup.send(
                embed=discord.Embed(
                    title=f"エントリー{action_ja}",
                    description=f"審査待ち: {len(entries)} 件\nリストからユーザーを選択してください。",
                    color=COLOR_INFO,
                ),
                view=view,
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EntryCog(bot))


async def _notify_late_entry_request(
    interaction: discord.Interaction,
    *,
    kukai_title: str,
    kukai_id: int,
    channel_id: int | None,
    admin_ids: list[int],
    display_name: str,
) -> None:
    """Notify kukai admins that a post-deadline entry needs approval."""
    assert interaction.guild is not None

    target = interaction.guild.get_channel(channel_id) if channel_id else interaction.channel
    if not target or not hasattr(target, "send"):
        return

    unique_admin_ids = list(dict.fromkeys(admin_ids))
    mentions = " ".join(f"<@{user_id}>" for user_id in unique_admin_ids)
    embed = discord.Embed(
        title="締切後エントリー申請",
        description=(
            f"「**{kukai_title}**」に締切後のエントリー申請がありました。\n"
            "承認する場合は `/entry approve`、却下する場合は `/entry reject` を実行してください。"
        ),
        color=COLOR_INFO,
    )
    embed.add_field(
        name="申請者",
        value=f"UID: `{interaction.user.id}`\n俳号: **{display_name}**",
        inline=False,
    )
    embed.set_footer(text=f"句会ID: {kukai_id}")

    try:
        await send_with_retry(
            lambda: target.send(
                content=mentions or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.warning("late entry admin notification failed: %s", error)
