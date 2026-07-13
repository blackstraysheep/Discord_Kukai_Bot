"""Selecting command: /select"""

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from bot.database import get_session
from bot.models.select_rule import SelectLabel
from bot.repositories import submission_repo
from bot.services import kukai_service, select_service
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.ui.select_view import build_select_entry_response
from bot.utils.bulk_parser import BulkParseError, parse_fields
from bot.utils.channel import effective_channel_id
from bot.utils.embed_builder import COLOR_INFO, error_embed


class SelectCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="select", description="選句します（投句を一覧表示して選句）")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def select(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                embed, view = await build_select_entry_response(session, kukai, interaction.user.id)

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @app_commands.command(name="select-bulk", description="複数行をまとめて選句します")
    @app_commands.describe(
        selections="番号=ラベル|コメント / overall=総評 / 番号=clear",
        kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）",
    )
    async def select_bulk(
        self,
        interaction: discord.Interaction,
        selections: str,
        kukai_id: int | None = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            rows = parse_fields(selections)
        except BulkParseError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        try:
            selected_count = 0
            cleared_count = 0
            overall_updated = False
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if KukaiState.from_value(kukai.state) != KukaiState.SELECTING_OPEN:
                    await interaction.response.send_message(
                        embed=error_embed("現在選句を受け付けていません。"), ephemeral=True
                    )
                    return

                pub_subs = await submission_repo.list_published(session, kukai.id)
                pub_by_number = {str(ps.number): ps for ps in pub_subs}

                result = await session.execute(
                    select(SelectLabel)
                    .where(SelectLabel.kukai_id == kukai.id)
                    .order_by(SelectLabel.display_order)
                )
                labels = list(result.scalars().all())
                if not any(lbl.label == "作者コメント" for lbl in labels):
                    session.add(
                        SelectLabel(
                            kukai_id=kukai.id,
                            template_id=None,
                            display_order=999,
                            label="作者コメント",
                            point=0,
                            rank_priority=999,
                            min_count=0,
                            max_count=None,
                            comment_mode="required",
                        )
                    )
                    await session.flush()
                    result = await session.execute(
                        select(SelectLabel)
                        .where(SelectLabel.kukai_id == kukai.id)
                        .order_by(SelectLabel.display_order)
                    )
                    labels = list(result.scalars().all())
                label_by_name = {label.label: label for label in labels}

                for row in rows:
                    if row.key == "overall":
                        await select_service.set_overall_comment(
                            session, kukai, interaction.user.id, row.value
                        )
                        overall_updated = True
                        continue

                    ps = pub_by_number.get(row.key)
                    if ps is None:
                        raise BulkParseError(f"{row.line_no}行目: 公開番号 {row.key} が見つかりません。")

                    if row.value.lower() == "clear":
                        await select_service.remove_select(
                            session, kukai, interaction.user.id, ps.submission_id
                        )
                        cleared_count += 1
                        continue

                    label_name, sep, comment = row.value.partition("|")
                    label_name = label_name.strip()
                    comment = comment.strip() if sep else None
                    label = label_by_name.get(label_name)
                    if label is None:
                        raise BulkParseError(f"{row.line_no}行目: ラベル「{label_name}」が見つかりません。")

                    await select_service.cast_select(
                        session,
                        kukai,
                        interaction.user.id,
                        ps.submission_id,
                        label.id,
                        comment=comment,
                        is_self_comment=(label.label == "作者コメント"),
                    )
                    selected_count += 1

        except (BulkParseError, ServiceError) as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        parts = [f"選句 {selected_count}件"]
        if cleared_count:
            parts.append(f"取消 {cleared_count}件")
        if overall_updated:
            parts.append("総評更新")
        await interaction.response.send_message(
            embed=discord.Embed(description=" / ".join(parts), color=COLOR_INFO),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelectCog(bot))
