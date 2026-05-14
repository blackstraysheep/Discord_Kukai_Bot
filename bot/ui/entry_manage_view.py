"""Admin UI for approving/rejecting entries from a select menu."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.database import get_session
from bot.services import entry_service, kukai_service
from bot.services.errors import ServiceError
from bot.utils.embed_builder import error_embed, success_embed

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
            status_tag = {"pending": "審査待", "approved": "承認済"}.get(entry.status, entry.status)
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
                    verb = "承認"
                else:
                    entry = await entry_service.reject(
                        session, kukai, interaction.user.id, target_user_id
                    )
                    verb = "却下"

            member = interaction.guild.get_member(target_user_id)
            name = (
                entry.haigo
                or (member.display_name if member else f"UID:{target_user_id}")
            )
            await interaction.response.send_message(
                embed=success_embed(f"**{name}** さんを{verb}しました。"),
                ephemeral=True,
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
