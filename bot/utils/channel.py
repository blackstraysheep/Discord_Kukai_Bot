"""Discord interaction channel utilities."""

from __future__ import annotations

import discord


def effective_channel_id(interaction: discord.Interaction) -> int | None:
    """Return the channel_id to use for kukai resolution.

    When the command is used inside a thread, use the parent channel so that
    kukai.channel_id (always set to the parent text channel) matches.
    """
    if isinstance(interaction.channel, discord.Thread):
        return interaction.channel.parent_id
    return interaction.channel_id
