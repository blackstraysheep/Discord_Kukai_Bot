"""Result display command: /result"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import kukai_service, permission_service, result_service
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.utils.discord_retry import send_with_retry
from bot.utils.embed_builder import COLOR_INFO, COLOR_RESULT, error_embed
from bot.utils.text import discord_safe

logger = logging.getLogger(__name__)

_PREVIEW_ALLOWED = {
    KukaiState.SELECTING_CLOSED,
    KukaiState.WAITING_RESULTS,
    KukaiState.RESULTS,
    KukaiState.ENDED,
}


def _score_embed(
    kukai,
    results,
    *,
    reveal_author_for_user: dict[int, bool],
    guild: discord.Guild,
) -> list[discord.Embed]:
    """Build result embeds sorted by rank (score desc)."""
    pages: list[discord.Embed] = []
    embed = discord.Embed(
        title=f"🏆 選句結果 — {kukai.title}",
        color=COLOR_RESULT,
    )
    char_count = len(embed.title)

    for r in results:
        author_line = ""
        if reveal_author_for_user.get(r.author_user_id, False):
            member = guild.get_member(r.author_user_id)
            author_name = member.display_name if member else f"UID:{r.author_user_id}"
            author_line = f"　作者: {discord_safe(author_name)}"

        label_parts = [f"{lv.label}×{lv.count}" for lv in r.label_selects]
        label_str = "　".join(label_parts) if label_parts else "（無選）"

        header = f"**{r.rank}位 ({r.total_score}pt)** — No.{r.number}{author_line}"
        body_lines = [
            f"> {discord_safe(r.text)}",
            label_str,
        ]
        # Inline comments (up to 3)
        for lv in r.label_selects:
            for comment in lv.comments[:3]:
                body_lines.append(f"　💬 [{lv.label}] {discord_safe(comment[:80])}")

        field_value = "\n".join(body_lines)

        # Paginate: Discord embed has 6000 char total limit, 25 fields max
        if len(embed.fields) >= 25 or char_count + len(header) + len(field_value) > 5800:
            pages.append(embed)
            embed = discord.Embed(color=COLOR_RESULT)
            char_count = 0

        embed.add_field(name=header, value=field_value, inline=False)
        char_count += len(header) + len(field_value)

    embed.set_footer(text=f"句会 ID: {kukai.id}　|　全 {len(results)} 句")
    pages.append(embed)
    return pages


def _number_embed(kukai, results, guild: discord.Guild) -> list[discord.Embed]:
    """Build result embeds sorted by submission number."""
    sorted_r = sorted(results, key=lambda r: r.number)
    embed = discord.Embed(
        title=f"📋 投句一覧（番号順） — {kukai.title}",
        color=COLOR_INFO,
    )
    lines = []
    for r in sorted_r:
        label_str = "　".join(f"{lv.label}×{lv.count}" for lv in r.label_selects) or "（無選）"
        lines.append(f"`No.{r.number}` {discord_safe(r.text)}　— {label_str} ({r.total_score}pt)")
    embed.description = "\n".join(lines[:40])
    if len(sorted_r) > 40:
        embed.set_footer(text=f"他 {len(sorted_r) - 40} 句　|　句会 ID: {kukai.id}")
    else:
        embed.set_footer(text=f"全 {len(sorted_r)} 句　|　句会 ID: {kukai.id}")
    return [embed]


def _author_embed(
    kukai,
    results,
    guild: discord.Guild,
    *,
    visible_author_ids: set[int],
) -> list[discord.Embed]:
    """Build result embeds grouped by author."""
    from collections import defaultdict
    by_author: dict[int, list] = defaultdict(list)
    for r in results:
        if r.author_user_id not in visible_author_ids:
            continue
        by_author[r.author_user_id].append(r)

    embed = discord.Embed(
        title=f"👤 選句結果（作者別） — {kukai.title}",
        color=COLOR_RESULT,
    )
    for user_id, subs in by_author.items():
        member = guild.get_member(user_id)
        author_name = member.display_name if member else f"UID:{user_id}"
        total = sum(r.total_score for r in subs)
        lines = [f"`No.{r.number}` {discord_safe(r.text)} — {r.total_score}pt ({r.rank}位)" for r in subs]
        embed.add_field(
            name=f"{discord_safe(author_name)} (合計 {total}pt)",
            value="\n".join(lines),
            inline=False,
        )
        if len(embed.fields) >= 25:
            break

    embed.set_footer(text=f"句会 ID: {kukai.id}")
    return [embed]


class ResultCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="result", description="句会の結果を表示します")
    @app_commands.describe(
        kukai_id="句会ID",
        format="表示形式 (score=点数順 / number=番号順 / author=作者別)",
    )
    @app_commands.choices(
        format=[
            app_commands.Choice(name="点数順", value="score"),
            app_commands.Choice(name="番号順", value="number"),
            app_commands.Choice(name="作者別", value="author"),
        ]
    )
    async def result(
        self,
        interaction: discord.Interaction,
        kukai_id: int,
        format: str = "score",
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                state = KukaiState.from_value(kukai.state)

                # Non-RESULTS states require admin
                if state != KukaiState.RESULTS:
                    if state not in _PREVIEW_ALLOWED:
                        await interaction.response.send_message(
                            embed=error_embed("結果をまだ表示できません。"), ephemeral=True
                        )
                        return
                    is_admin = await permission_service.is_kukai_admin(
                        session, kukai, interaction.user  # type: ignore[arg-type]
                    )
                    if not is_admin:
                        await interaction.response.send_message(
                            embed=error_embed("結果は公開後に閲覧できます。"), ephemeral=True
                        )
                        return

                results = await result_service.compute_results(session, kukai)

            if not results:
                await interaction.response.send_message(
                    embed=discord.Embed(description="集計対象の投句がありません。", color=COLOR_INFO),
                    ephemeral=True,
                )
                return

            reveal = kukai.author_reveal
            # author_reveal_zero=False: hide authors with score <= 0
            if format == "score":
                totals: dict[int, int] = {}
                for r in results:
                    totals[r.author_user_id] = totals.get(r.author_user_id, 0) + r.total_score
                if not kukai.author_reveal:
                    visible_author_ids: set[int] = set()
                elif kukai.author_reveal_zero:
                    visible_author_ids = set(totals.keys())
                else:
                    visible_author_ids = {uid for uid, score in totals.items() if score > 0}
                reveal_map = {uid: uid in visible_author_ids for uid in totals.keys()}
                embeds = _score_embed(
                    kukai,
                    results,
                    reveal_author_for_user=reveal_map,
                    guild=interaction.guild,
                )
            elif format == "number":
                embeds = _number_embed(kukai, results, guild=interaction.guild)
            else:
                if not reveal:
                    await interaction.response.send_message(
                        embed=error_embed("この句会は作者非公開に設定されています。"), ephemeral=True
                    )
                    return
                totals: dict[int, int] = {}
                for r in results:
                    totals[r.author_user_id] = totals.get(r.author_user_id, 0) + r.total_score
                if kukai.author_reveal_zero:
                    visible_author_ids = set(totals.keys())
                else:
                    visible_author_ids = {uid for uid, score in totals.items() if score > 0}
                if not visible_author_ids:
                    await interaction.response.send_message(
                        embed=error_embed("公開対象の作者がいないため、作者別表示はできません。"),
                        ephemeral=True,
                    )
                    return
                embeds = _author_embed(
                    kukai,
                    results,
                    guild=interaction.guild,
                    visible_author_ids=visible_author_ids,
                )

            # Public in RESULTS state, ephemeral otherwise
            ephemeral = state != KukaiState.RESULTS
            await send_with_retry(
                lambda: interaction.response.send_message(embed=embeds[0], ephemeral=ephemeral)
            )
            for extra in embeds[1:]:
                await asyncio.sleep(0.35)
                await send_with_retry(
                    lambda: interaction.followup.send(embed=extra, ephemeral=ephemeral)
                )

        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
        except discord.Forbidden:
            msg = "メッセージ送信権限が不足しているため、結果表示に失敗しました。"
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed(msg), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed(msg), ephemeral=True)
        except discord.HTTPException as e:
            logger.warning("result command send failed: %s", e)
            msg = "結果の送信中に一時的な通信エラーが発生しました。再実行してください。"
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed(msg), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed(msg), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ResultCog(bot))
