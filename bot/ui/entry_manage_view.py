"""Admin UI for approving/rejecting entries from a select menu."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.database import get_session
from bot.services import channel_visibility_service, entry_service, kukai_service
from bot.services.errors import ServiceError
from bot.utils.embed_builder import error_embed, success_embed
from bot.utils.entry_notifications import notify_entry_approved
from bot.utils.entry_notifications import notify_entry_rejected

if TYPE_CHECKING:
    from bot.models.entry import Entry


class EntryActionSelect(discord.ui.Select):
    """Select a user from pending entries, then immediately apply an action."""

    def __init__(
        self,
        kukai_id: int,
        entries: list[Entry],
        guild: discord.Guild,
        action: str,  # 'approve' | 'reject'
    ) -> None:
        self.kukai_id = kukai_id
        self.action = action

        options = []
        for entry in entries[:25]:
            member = guild.get_member(entry.user_id)
            display = entry.haigo or (member.display_name if member else f"UID:{entry.user_id}")
            status_tag = {"pending": "承認待ち", "approved": "承認済"}.get(entry.status, entry.status)
            options.append(
                discord.SelectOption(
                    label=display[:100],
                    value=str(entry.user_id),
                    description=f"[{status_tag}] {entry.user_id}",
                )
            )

        action_label = "承認" if action == "approve" else "却下"
        super().__init__(
            placeholder=f"{action_label}するユーザーを選択…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        target_user_id = int(self.values[0])
        assert interaction.guild is not None

        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(
                    session, self.kukai_id, interaction.guild.id
                )
                if self.action == "approve":
                    entry = await entry_service.approve(
                        session, kukai, interaction.user.id, target_user_id
                    )
                    visibility_result = await channel_visibility_service.grant_entry_access(
                        session,
                        interaction.guild,
                        kukai,
                        entry,
                    )
                    verb = "承認"
                else:
                    entry = await entry_service.reject(
                        session, kukai, interaction.user.id, target_user_id
                    )
                    visibility_result = await channel_visibility_service.revoke_entry_access(
                        session,
                        interaction.guild,
                        kukai,
                        target_user_id,
                    )
                    verb = "却下"

            member = interaction.guild.get_member(target_user_id)
            name = (
                entry.haigo
                or (member.display_name if member else f"UID:{target_user_id}")
            )
            await interaction.response.send_message(
                embed=success_embed(
                    f"**{name}** さんを{verb}しました。"
                    + _visibility_sync_warning(visibility_result)
                ),
                ephemeral=True,
            )
            if self.action == "approve":
                await notify_entry_approved(
                    interaction.guild,
                    kukai,
                    user_id=target_user_id,
                    display_name=name,
                )
            else:
                await notify_entry_rejected(
                    interaction.guild,
                    kukai,
                    display_name=name,
                )
        except ServiceError as e:
            await interaction.response.send_message(
                embed=error_embed(str(e)), ephemeral=True
            )


class EntryManageView(discord.ui.View):
    """Ephemeral view for admin entry management."""

    def __init__(
        self,
        kukai_id: int,
        entries: list[Entry],
        guild: discord.Guild,
        action: str,
    ) -> None:
        super().__init__(timeout=120)
        if entries:
            self.add_item(EntryActionSelect(kukai_id, entries, guild, action))


class LateEntryReviewView(discord.ui.View):
    """Persistent-enough admin buttons for one late entry request."""

    def __init__(self, kukai_id: int, target_user_id: int, display_name: str) -> None:
        super().__init__(timeout=86400)
        self.kukai_id = kukai_id
        self.target_user_id = target_user_id
        self.display_name = display_name

    @discord.ui.button(label="却下", style=discord.ButtonStyle.danger)
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._apply(interaction, "reject")

    @discord.ui.button(label="承認", style=discord.ButtonStyle.success)
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._apply(interaction, "approve")

    async def _apply(self, interaction: discord.Interaction, action: str) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(
                    session, self.kukai_id, interaction.guild.id
                )
                if action == "approve":
                    entry = await entry_service.approve(
                        session, kukai, interaction.user.id, self.target_user_id
                    )
                    visibility_result = await channel_visibility_service.grant_entry_access(
                        session,
                        interaction.guild,
                        kukai,
                        entry,
                    )
                    approved = True
                    verb = "承認"
                else:
                    entry = await entry_service.reject(
                        session, kukai, interaction.user.id, self.target_user_id
                    )
                    visibility_result = await channel_visibility_service.revoke_entry_access(
                        session,
                        interaction.guild,
                        kukai,
                        self.target_user_id,
                    )
                    approved = False
                    verb = "却下"

            member = interaction.guild.get_member(self.target_user_id)
            name = entry.haigo or (member.display_name if member else self.display_name)
            for child in self.children:
                child.disabled = True  # type: ignore[attr-defined]
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                embed=success_embed(
                    f"**{name}** さんを{verb}しました。"
                    + _visibility_sync_warning(visibility_result)
                ),
                ephemeral=True,
            )
            if approved:
                await notify_entry_approved(
                    interaction.guild,
                    kukai,
                    user_id=self.target_user_id,
                    display_name=name,
                )
            else:
                await notify_entry_rejected(
                    interaction.guild,
                    kukai,
                    display_name=name,
                )
        except ServiceError as e:
            await interaction.response.send_message(
                embed=error_embed(str(e)), ephemeral=True
            )


def _visibility_sync_warning(result) -> str:
    if result is None or result.ok:
        return ""
    return "\n\n⚠️ 参加状態は更新済みですが、チャンネル権限同期に失敗しました。`/kukai visibility-sync` を実行してください。"
