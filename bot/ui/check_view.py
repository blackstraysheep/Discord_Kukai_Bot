"""Paginated personal kukai status view."""

from __future__ import annotations

import discord

from bot.utils.embed_builder import error_embed


class CheckPagerView(discord.ui.View):
    def __init__(self, *, user_id: int, pages: list[discord.Embed]) -> None:
        super().__init__(timeout=300)
        self.user_id = user_id
        self.pages = pages
        self.index = 0
        self.prev_button = discord.ui.Button(
            label="前へ",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        self.next_button = discord.ui.Button(
            label="次へ",
            style=discord.ButtonStyle.secondary,
            disabled=len(pages) <= 1,
        )
        self.prev_button.callback = self._on_prev
        self.next_button.callback = self._on_next
        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    @classmethod
    def for_pages(cls, *, user_id: int, pages: list[discord.Embed]) -> "CheckPagerView | None":
        if len(pages) <= 1:
            return None
        return cls(user_id=user_id, pages=pages)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if not await self._ensure_owner(interaction):
            return
        self.index = max(0, self.index - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if not await self._ensure_owner(interaction):
            return
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            embed=error_embed("この確認UIは呼び出した本人だけが操作できます。"),
            ephemeral=True,
        )
        return False

    def _sync_buttons(self) -> None:
        self.prev_button.disabled = self.index <= 0
        self.next_button.disabled = self.index >= len(self.pages) - 1
