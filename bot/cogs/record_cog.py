"""Participation record commands."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.formatters.participation_record_embed_formatter import (
    build_participation_record_summary_embed,
)
from bot.formatters.participation_record_markdown_exporter import (
    build_participation_record_markdown,
)
from bot.services.errors import ServiceError, ValidationError
from bot.services.participation_record_service import get_participation_records
from bot.utils.embed_builder import error_embed

RecordScopeOption = Literal["current", "all"]
RecordGroupOption = Literal["kukai", "server", "haigo"]
OtherRecordGroupOption = Literal["kukai", "haigo"]


class RecordCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    record = app_commands.Group(name="record", description="参加記録")

    @record.command(name="me", description="自分の参加記録を表示します")
    @app_commands.describe(
        scope="表示範囲",
        group_by="表示軸",
        haigo="俳号の完全一致フィルタ",
        limit="Discord上の要約件数",
    )
    async def record_me(
        self,
        interaction: discord.Interaction,
        scope: RecordScopeOption = "current",
        group_by: RecordGroupOption = "kukai",
        haigo: str | None = None,
        limit: app_commands.Range[int, 1, 25] = 5,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            await self._send_record(
                interaction,
                target=interaction.user,
                scope=scope,
                group_by=group_by,
                haigo=haigo,
                limit=limit,
            )
        except ServiceError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)

    @record.command(name="user", description="同一サーバ内の他参加者記録を表示します")
    @app_commands.describe(
        user="対象ユーザー",
        group_by="表示軸",
        haigo="俳号の完全一致フィルタ",
        limit="Discord上の要約件数",
    )
    async def record_user(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        group_by: OtherRecordGroupOption = "kukai",
        haigo: str | None = None,
        limit: app_commands.Range[int, 1, 25] = 5,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        if user.bot:
            await interaction.followup.send(
                embed=error_embed("Botユーザーの参加記録は表示できません。"),
                ephemeral=True,
            )
            return
        try:
            await self._send_record(
                interaction,
                target=user,
                scope="current",
                group_by=group_by,
                haigo=haigo,
                limit=limit,
            )
        except ServiceError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)

    async def _send_record(
        self,
        interaction: discord.Interaction,
        *,
        target: discord.abc.User,
        scope: RecordScopeOption,
        group_by: RecordGroupOption,
        haigo: str | None,
        limit: int,
    ) -> None:
        await send_participation_record(
            interaction,
            bot=self.bot,
            target=target,
            scope=scope,
            group_by=group_by,
            haigo=haigo,
            limit=limit,
        )

    def _guild_names(
        self,
        records,
        *,
        current_guild: discord.Guild,
    ) -> dict[int, str]:
        names: dict[int, str] = {current_guild.id: current_guild.name}
        for record in records:
            guild = self.bot.get_guild(record.guild_id)
            if guild is not None:
                names[record.guild_id] = guild.name
        return names


def _record_filename(user_id: int) -> str:
    date = datetime.now().strftime("%Y%m%d")
    return f"participation-records-{user_id}-{date}.md"


def _markdown_file(filename: str, markdown: str) -> discord.File:
    data = BytesIO(markdown.encode("utf-8"))
    return discord.File(data, filename=filename)


async def send_participation_record(
    interaction: discord.Interaction,
    *,
    bot: commands.Bot | discord.Client,
    target: discord.abc.User,
    scope: RecordScopeOption,
    group_by: RecordGroupOption,
    haigo: str | None,
    limit: int,
) -> None:
    """Send the shared `/record` response to an interaction followup."""
    assert interaction.guild is not None
    target_display_name = getattr(target, "display_name", target.name)
    async with get_session() as session:
        result = await get_participation_records(
            session,
            current_guild_id=interaction.guild.id,
            target_user_id=target.id,
            target_display_name=target_display_name,
            viewer_user_id=interaction.user.id,
            scope=scope,
            group_by=group_by,
            haigo=haigo.strip() if haigo and haigo.strip() else None,
        )

    guild_names: dict[int, str] = {interaction.guild.id: interaction.guild.name}
    for record in result.records:
        guild = bot.get_guild(record.guild_id)
        if guild is not None:
            guild_names[record.guild_id] = guild.name
    filename = _record_filename(target.id)
    markdown = build_participation_record_markdown(result, guild_names=guild_names)
    embed = build_participation_record_summary_embed(
        result,
        guild_names=guild_names,
        limit=limit,
        filename=filename,
    )
    await interaction.followup.send(
        embed=embed,
        file=_markdown_file(filename, markdown),
        ephemeral=True,
    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RecordCog(bot))
