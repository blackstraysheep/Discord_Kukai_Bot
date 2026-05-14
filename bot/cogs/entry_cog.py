"""Entry commands: /entry *

Note: Discord requires all subcommands under the same group namespace.
The original requirements listed '/entry' (join) without a subcommand, but
Discord does not allow a group and a same-name leaf command to coexist.
We use '/entry join' for consistency.
"""

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import entry_service, kukai_service, permission_service
from bot.services.errors import ServiceError
from bot.ui.entry_manage_view import EntryManageView
from bot.utils.embed_builder import (
    COLOR_INFO,
    error_embed,
    success_embed,
)


class EntryHaigoModal(discord.ui.Modal, title="エントリー"):
    haigo = discord.ui.TextInput(
        label="俳号（ペンネーム）",
        placeholder="空欄の場合はサーバーの表示名を使用します",
        required=False,
        max_length=100,
    )

    def __init__(self, kukai_id: int) -> None:
        super().__init__()
        self.kukai_id = kukai_id
        self._result_embed: discord.Embed | None = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None

        haigo = self.haigo.value.strip() or None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(
                    session, self.kukai_id, interaction.guild.id
                )
                entry = await entry_service.enter(
                    session, kukai, interaction.user.id, haigo
                )
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
    @app_commands.describe(kukai_id="句会ID")
    async def entry_join(self, interaction: discord.Interaction, kukai_id: int) -> None:
        await interaction.response.send_modal(EntryHaigoModal(kukai_id))

    @entry.command(name="cancel", description="エントリーを取り消します（受付期間中のみ）")
    @app_commands.describe(kukai_id="句会ID")
    async def entry_cancel(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                entry = await entry_service.withdraw(session, kukai, interaction.user.id)
            await interaction.response.send_message(
                embed=success_embed(f"「{kukai.title}」のエントリーを取り消しました。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------

    @entry.command(name="list", description="【管理者】エントリー一覧を表示します")
    @app_commands.describe(kukai_id="句会ID", status="絞り込み (省略で全件)")
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
        kukai_id: int,
        status: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                entries = await entry_service.list_entries(session, kukai_id, status)

            if not entries:
                label = f"（{status}）" if status else ""
                await interaction.response.send_message(
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
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @entry.command(name="approve", description="【管理者】エントリーを承認します")
    @app_commands.describe(kukai_id="句会ID", user="承認するユーザー（省略でリストから選択）")
    async def entry_approve(
        self,
        interaction: discord.Interaction,
        kukai_id: int,
        user: discord.Member | None = None,
    ) -> None:
        await self._admin_action(interaction, kukai_id, user, "approve")

    @entry.command(name="reject", description="【管理者】エントリーを却下します")
    @app_commands.describe(kukai_id="句会ID", user="却下するユーザー（省略でリストから選択）")
    async def entry_reject(
        self,
        interaction: discord.Interaction,
        kukai_id: int,
        user: discord.Member | None = None,
    ) -> None:
        await self._admin_action(interaction, kukai_id, user, "reject")

    @entry.command(name="remove", description="【管理者】エントリーを削除します（エントリー締切後）")
    @app_commands.describe(kukai_id="句会ID", user="削除するユーザー")
    async def entry_remove(
        self,
        interaction: discord.Interaction,
        kukai_id: int,
        user: discord.Member,
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                await entry_service.admin_remove(session, kukai, user.id)
            await interaction.response.send_message(
                embed=success_embed(f"{user.display_name} さんのエントリーを削除しました。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    async def _admin_action(
        self,
        interaction: discord.Interaction,
        kukai_id: int,
        user: discord.Member | None,
        action: str,
    ) -> None:
        assert interaction.guild is not None
        action_ja = "承認" if action == "approve" else "却下"
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return

                if user is not None:
                    # Direct action on specified user
                    if action == "approve":
                        entry = await entry_service.approve(
                            session, kukai, interaction.user.id, user.id
                        )
                    else:
                        entry = await entry_service.reject(
                            session, kukai, interaction.user.id, user.id
                        )
                    name = entry.haigo or user.display_name
                    await interaction.response.send_message(
                        embed=success_embed(f"**{name}** さんを{action_ja}しました。"),
                        ephemeral=True,
                    )
                    return

                # No user specified → show select menu of pending entries
                entries = await entry_service.list_entries(session, kukai_id, status="pending")

            if not entries:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="審査待ちのエントリーはありません。",
                        color=COLOR_INFO,
                    ),
                    ephemeral=True,
                )
                return

            view = EntryManageView(kukai_id, entries, interaction.guild, action)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=f"エントリー{action_ja}",
                    description=f"審査待ち: {len(entries)} 件\nリストからユーザーを選択してください。",
                    color=COLOR_INFO,
                ),
                view=view,
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EntryCog(bot))
