"""Reusable Discord UI components."""

import discord


class ConfirmView(discord.ui.View):
    """Two-button ephemeral confirmation dialog.

    Usage:
        view = ConfirmView()
        await interaction.response.send_message("本当に実行しますか？", view=view, ephemeral=True)
        await view.wait()
        if view.confirmed:
            ...
    """

    def __init__(self, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.confirmed: bool = False

    @discord.ui.button(label="実行", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.confirmed = False
        self.stop()
        await interaction.response.defer()
