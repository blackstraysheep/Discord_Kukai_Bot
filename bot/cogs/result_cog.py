"""Result display command: /result"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.repositories import entry_repo, participant_repo, select_repo
from bot.services import kukai_service, permission_service, result_service
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.utils.channel import effective_channel_id
from bot.utils.discord_retry import send_with_retry
from bot.utils.embed_builder import COLOR_INFO, COLOR_RESULT, error_embed
from bot.utils.submission_markup import discord_safe_submission_text
from bot.utils.text import discord_safe

logger = logging.getLogger(__name__)
COMMENT_PREVIEW_LIMIT = 300
OVERALL_PREVIEW_LIMIT = 1000

_PREVIEW_ALLOWED = {
    KukaiState.SELECTING_CLOSED,
    KukaiState.RESULTS,
    KukaiState.ENDED,
}

_FORMAT_LABELS = {
    "score": "点数順",
    "number": "番号順",
    "author": "作者別",
}


def _score_embed(
    kukai,
    results,
    *,
    reveal_author_for_user: dict[int, bool],
    guild: discord.Guild,
    display_names: dict[int, str],
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
            author_name = _display_name(r.author_user_id, guild, display_names)
            author_line = f"　作者: {discord_safe(author_name)}"

        label_parts = [_label_select_summary(lv, guild, display_names) for lv in r.label_selects]
        label_str = "　".join(label_parts) if label_parts else "（無選）"

        header = f"**{r.rank}位 ({r.total_score}点)** — No.{r.number}{author_line}"
        body_lines = [
            f"> {discord_safe_submission_text(r.text)}",
            label_str,
        ]
        # Inline comments (up to 3)
        for lv in r.label_selects:
            for comment in lv.comments[:3]:
                body_lines.append(
                    f"　💬 [{lv.label}] {discord_safe(comment.text[:COMMENT_PREVIEW_LIMIT])}"
                    f"（{_comment_signature(comment.selector_user_id, guild, display_names)}）"
                )

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


def _number_embed(
    kukai,
    results,
    guild: discord.Guild,
    display_names: dict[int, str],
) -> list[discord.Embed]:
    """Build result embeds sorted by submission number."""
    sorted_r = sorted(results, key=lambda r: r.number)
    embed = discord.Embed(
        title=f"📋 投句一覧（番号順） — {kukai.title}",
        color=COLOR_INFO,
    )
    lines = []
    for r in sorted_r:
        label_str = "　".join(
            _label_select_summary(lv, guild, display_names) for lv in r.label_selects
        ) or "（無選）"
        line = f"`No.{r.number}` {discord_safe_submission_text(r.text)}　— {label_str} ({r.total_score}点)"
        author_comments = [c for lv in r.label_selects if lv.label == "作者コメント" for c in lv.comments[:1]]
        if author_comments:
            line += f"\n　🖊 作者コメント: {discord_safe(author_comments[0].text[:COMMENT_PREVIEW_LIMIT])}"
        lines.append(line)
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
    display_names: dict[int, str],
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
        author_name = _display_name(user_id, guild, display_names)
        total = sum(r.total_score for r in subs)
        lines = []
        for r in subs:
            line = f"`No.{r.number}` {discord_safe_submission_text(r.text)} — {r.total_score}点 ({r.rank}位)"
            author_comments = [c for lv in r.label_selects if lv.label == "作者コメント" for c in lv.comments[:1]]
            if author_comments:
                line += f"\n　🖊 作者コメント: {discord_safe(author_comments[0].text[:COMMENT_PREVIEW_LIMIT])}"
            lines.append(line)
        embed.add_field(
            name=f"{discord_safe(author_name)} (合計 {total}点)",
            value="\n".join(lines),
            inline=False,
        )
        if len(embed.fields) >= 25:
            break

    embed.set_footer(text=f"句会 ID: {kukai.id}")
    return [embed]


def _overall_embeds(
    kukai,
    overall_comments,
    guild: discord.Guild,
    display_names: dict[int, str],
) -> list[discord.Embed]:
    if not overall_comments:
        return []

    pages: list[discord.Embed] = []
    embed = discord.Embed(
        title=f"📝 総評 — {kukai.title}",
        color=COLOR_INFO,
    )
    char_count = len(embed.title)

    for overall in overall_comments:
        user_name = _display_name(overall.user_id, guild, display_names)
        header = discord_safe(user_name)
        body = discord_safe(overall.comment[:OVERALL_PREVIEW_LIMIT])

        if len(embed.fields) >= 25 or char_count + len(header) + len(body) > 5800:
            pages.append(embed)
            embed = discord.Embed(color=COLOR_INFO)
            char_count = 0

        embed.add_field(name=header, value=body, inline=False)
        char_count += len(header) + len(body)

    embed.set_footer(text=f"句会 ID: {kukai.id}　|　総評 {len(overall_comments)} 件")
    pages.append(embed)
    return pages


def _available_formats(kukai) -> list[str]:
    formats: list[str] = []
    if kukai.points_enabled:
        formats.append("score")
    formats.append("number")
    if kukai.author_reveal:
        formats.append("author")
    return formats


def _resolve_initial_format(kukai, requested: str | None) -> str:
    available = _available_formats(kukai)
    if not available:
        return "number"

    if requested and requested in available:
        return requested

    default_format = kukai.result_display_default if kukai.result_display_default in available else None
    if default_format:
        return default_format

    return available[0]


def _display_name(user_id: int, guild: discord.Guild, display_names: dict[int, str]) -> str:
    if user_id in display_names:
        return display_names[user_id]
    member = guild.get_member(user_id)
    return member.display_name if member else f"UID:{user_id}"


def _label_select_summary(label_select, guild: discord.Guild, display_names: dict[int, str]) -> str:
    selector_names = [
        discord_safe(_display_name(user_id, guild, display_names))
        for user_id in label_select.selector_user_ids
    ]
    selector_suffix = f"（{'・'.join(selector_names)}）" if selector_names else ""
    return f"{discord_safe(label_select.label)}×{label_select.count}{selector_suffix}"


def _comment_signature(user_id: int, guild: discord.Guild, display_names: dict[int, str]) -> str:
    return discord_safe(_display_name(user_id, guild, display_names))


async def _load_display_names(session, kukai_id: int, guild: discord.Guild) -> dict[int, str]:
    entries = await entry_repo.list_by_kukai(session, kukai_id)
    names: dict[int, str] = {}
    for entry in entries:
        member = guild.get_member(entry.user_id)
        names[entry.user_id] = entry.haigo or (member.display_name if member else f"UID:{entry.user_id}")
    participants = await participant_repo.list_by_kukai(session, kukai_id)
    for participant in participants:
        if participant.user_id in names:
            continue
        member = guild.get_member(participant.user_id)
        names[participant.user_id] = participant.haigo or (
            member.display_name if member else f"UID:{participant.user_id}"
        )
    return names


def build_result_entry_embed(kukai, *, result_count: int) -> discord.Embed:
    author_mode = getattr(kukai, "author_publication_mode", "with_result")
    if getattr(kukai, "author_reveal", False):
        author_note = "作者も公開されています。"
    elif author_mode == "manual":
        author_note = "作者はまだ公開されていません。管理者が後で公開できます。"
    elif author_mode == "never":
        author_note = "作者は公開されません。"
    else:
        author_note = "作者は公開されていません。"
    embed = discord.Embed(
        title=f"🏆 選句結果 — {kukai.title}",
        description=f"結果を見るボタンから個別に表示できます。\n{author_note}",
        color=COLOR_RESULT,
    )
    embed.set_footer(text=f"句会 ID: {kukai.id}　|　全 {result_count} 句")
    return embed


def result_open_custom_id(kukai_id: int, initial_format: str | None = None) -> str:
    format_token = initial_format or "default"
    return f"kukai:result:{kukai_id}:{format_token}"


class ResultOpenView(discord.ui.View):
    def __init__(self, kukai_id: int, *, initial_format: str | None = None) -> None:
        super().__init__(timeout=None)
        self.kukai_id = kukai_id
        self.initial_format = initial_format

        button = discord.ui.Button(
            label="結果を見る",
            style=discord.ButtonStyle.primary,
            row=0,
            custom_id=result_open_custom_id(kukai_id, initial_format),
        )
        button.callback = self._on_open
        self.add_item(button)

    async def _on_open(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.NotFound:
            logger.warning(
                "ResultOpenView interaction expired before defer (kukai_id=%s interaction_id=%s)",
                self.kukai_id,
                getattr(interaction, "id", None),
            )
            return

        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                state = KukaiState.from_value(kukai.state)
                if state not in {KukaiState.RESULTS, KukaiState.ENDED}:
                    await interaction.edit_original_response(
                        embed=error_embed("結果はまだ公開されていません。"),
                    )
                    return
                results = await result_service.compute_results(session, kukai)
                overall_comments = await select_repo.list_overall_comments(session, kukai.id)
                display_names = await _load_display_names(session, kukai.id, interaction.guild)
        except ServiceError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)))
            return

        if not results:
            await interaction.edit_original_response(
                embed=discord.Embed(description="集計対象の投句がありません。", color=COLOR_INFO),
            )
            return

        view = ResultSwitchView(
            kukai,
            results,
            overall_comments,
            interaction.guild,
            display_names,
            initial_format=_resolve_initial_format(kukai, self.initial_format),
        )
        await interaction.edit_original_response(
            embed=view.current_embed(),
            view=view,
        )


class _ResultFormatSelect(discord.ui.Select):
    def __init__(self, owner: "ResultSwitchView") -> None:
        self._owner = owner
        options = [
            discord.SelectOption(
                label=_FORMAT_LABELS.get(fmt, fmt),
                value=fmt,
                default=(fmt == owner.current_format),
            )
            for fmt in owner.available_formats
        ]
        super().__init__(
            placeholder="表示形式を切替",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self._owner.current_format = self.values[0]
        self._owner.current_page = 0
        self._owner._build_items()
        await interaction.response.edit_message(
            embed=self._owner.current_embed(),
            view=self._owner,
        )


class ResultSwitchView(discord.ui.View):
    def __init__(
        self,
        kukai,
        results,
        overall_comments,
        guild: discord.Guild,
        display_names: dict[int, str],
        *,
        initial_format: str,
    ) -> None:
        super().__init__(timeout=1800)
        self.kukai = kukai
        self.results = results
        self.overall_comments = overall_comments
        self.guild = guild
        self.display_names = display_names
        self.available_formats = _available_formats(kukai)
        self.current_format = _resolve_initial_format(kukai, initial_format)
        self.current_page = 0
        self._pages_cache: dict[str, list[discord.Embed]] = {}
        self._build_items()

    def _pages_for(self, fmt: str) -> list[discord.Embed]:
        cached = self._pages_cache.get(fmt)
        if cached is not None:
            return cached

        if fmt == "score":
            totals: dict[int, int] = {}
            for r in self.results:
                totals[r.author_user_id] = totals.get(r.author_user_id, 0) + r.total_score
            if not self.kukai.author_reveal:
                visible_author_ids: set[int] = set()
            elif self.kukai.author_reveal_zero:
                visible_author_ids = set(totals.keys())
            else:
                visible_author_ids = {uid for uid, score in totals.items() if score > 0}
            reveal_map = {uid: uid in visible_author_ids for uid in totals.keys()}
            pages = _score_embed(
                self.kukai,
                self.results,
                reveal_author_for_user=reveal_map,
                guild=self.guild,
                display_names=self.display_names,
            )
        elif fmt == "number":
            pages = _number_embed(
                self.kukai,
                self.results,
                guild=self.guild,
                display_names=self.display_names,
            )
        elif fmt == "author":
            totals: dict[int, int] = {}
            for r in self.results:
                totals[r.author_user_id] = totals.get(r.author_user_id, 0) + r.total_score
            if self.kukai.author_reveal_zero:
                visible_author_ids = set(totals.keys())
            else:
                visible_author_ids = {uid for uid, score in totals.items() if score > 0}
            if not visible_author_ids:
                pages = [
                    discord.Embed(
                        description="公開対象の作者がいないため、作者別表示はできません。",
                        color=COLOR_INFO,
                    )
                ]
            else:
                pages = _author_embed(
                    self.kukai,
                    self.results,
                    guild=self.guild,
                    visible_author_ids=visible_author_ids,
                    display_names=self.display_names,
                )
        else:
            pages = [discord.Embed(description="不明な表示形式です。", color=COLOR_INFO)]

        pages.extend(
            _overall_embeds(
                self.kukai,
                self.overall_comments,
                guild=self.guild,
                display_names=self.display_names,
            )
        )
        self._pages_cache[fmt] = pages
        return pages

    def current_embed(self) -> discord.Embed:
        pages = self._pages_for(self.current_format)
        index = min(max(self.current_page, 0), len(pages) - 1)
        self.current_page = index
        return pages[index]

    def _build_items(self) -> None:
        self.clear_items()
        self.add_item(_ResultFormatSelect(self))

        pages = self._pages_for(self.current_format)
        total_pages = len(pages)

        prev_btn = discord.ui.Button(
            label="◀",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=(self.current_page <= 0),
        )
        prev_btn.callback = self._on_prev
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label="▶",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=(self.current_page >= total_pages - 1),
        )
        next_btn.callback = self._on_next
        self.add_item(next_btn)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
        self._build_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        pages = self._pages_for(self.current_format)
        if self.current_page < len(pages) - 1:
            self.current_page += 1
        self._build_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


class ResultCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="result", description="句会の結果を表示します")
    @app_commands.describe(
        kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）",
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
        kukai_id: int | None = None,
        format: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
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
                overall_comments = await select_repo.list_overall_comments(session, kukai.id)
                display_names = await _load_display_names(session, kukai.id, interaction.guild)

            if not results:
                await interaction.response.send_message(
                    embed=discord.Embed(description="集計対象の投句がありません。", color=COLOR_INFO),
                    ephemeral=True,
                )
                return

            requested_format = format
            if requested_format == "score" and not kukai.points_enabled:
                await interaction.response.send_message(
                    embed=error_embed("この句会は点数制OFFのため、点数順表示はできません。"),
                    ephemeral=True,
                )
                return
            if requested_format == "author" and not kukai.author_reveal:
                await interaction.response.send_message(
                    embed=error_embed("この句会は作者非公開に設定されています。"),
                    ephemeral=True,
                )
                return

            view = ResultSwitchView(
                kukai,
                results,
                overall_comments,
                interaction.guild,
                display_names,
                initial_format=_resolve_initial_format(kukai, requested_format),
            )

            # Public in RESULTS state, ephemeral otherwise
            ephemeral = state != KukaiState.RESULTS
            if not ephemeral:
                await send_with_retry(
                    lambda: interaction.response.send_message(
                        embed=build_result_entry_embed(kukai, result_count=len(results)),
                        view=ResultOpenView(
                            kukai.id,
                            initial_format=_resolve_initial_format(kukai, requested_format),
                        ),
                    )
                )
                return
            await send_with_retry(
                lambda: interaction.response.send_message(
                    embed=view.current_embed(),
                    view=view,
                    ephemeral=ephemeral,
                )
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
