"""Kukai management commands: /kukai *"""

import asyncio

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.repositories import select_repo
from bot.services import (
    kukai_service,
    notification_service,
    permission_service,
    result_service,
    select_rule_service,
    submission_service,
)
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.ui.common import ConfirmView
from bot.ui.submission_view import RollbackView
from bot.utils.discord_retry import send_with_retry
from bot.utils.datetime_utils import format_jst, parse_datetime
from bot.utils.submission_publish import build_submission_publish_embeds
from bot.utils.embed_builder import (
    COLOR_INFO,
    COLOR_RESULT,
    COLOR_SUCCESS,
    error_embed,
    success_embed,
)
from bot.utils.result_publish import build_result_publish_embeds

# Japanese labels for each state
STATE_LABEL: dict[str, str] = {
    "draft": "下書き",
    "entry_open": "エントリー受付中",
    "entry_closed": "エントリー締切",
    "submission_open": "投句受付中",
    "submission_closed": "投句締切",
    "waiting_publish": "投句公開待ち",
    "selecting_open": "選句受付中",
    "selecting_closed": "選句締切",
    "results": "結果公開中",
    "ended": "終了",
    "paused": "一時停止",
    "cancelled": "中止",
}


class StageActionView(discord.ui.View):
    def __init__(self, kukai_id: int, state: KukaiState) -> None:
        super().__init__(timeout=86400)
        label_map = {
            KukaiState.ENTRY_OPEN: "エントリーする",
            KukaiState.SUBMISSION_OPEN: "投句する",
            KukaiState.SELECTING_OPEN: "選句する",
            KukaiState.RESULTS: "結果を見る",
        }
        command_map = {
            KukaiState.ENTRY_OPEN: f"/entry join kukai_id:{kukai_id}",
            KukaiState.SUBMISSION_OPEN: f"/submit kukai_id:{kukai_id}",
            KukaiState.SELECTING_OPEN: f"/select kukai_id:{kukai_id}",
            KukaiState.RESULTS: f"/result kukai_id:{kukai_id}",
        }
        button_label = label_map.get(state)
        command = command_map.get(state)
        if not button_label or not command:
            return
        button = discord.ui.Button(label=button_label, style=discord.ButtonStyle.primary, row=0)

        async def _callback(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"このチャンネルでは次を実行してください:\n`{command}`",
                    color=COLOR_INFO,
                ),
                ephemeral=True,
            )

        button.callback = _callback
        self.add_item(button)




class KukaiCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    kukai = app_commands.Group(name="kukai", description="句会の管理")

    # ------------------------------------------------------------------
    # Participant commands
    # ------------------------------------------------------------------

    @kukai.command(name="list", description="このサーバーの開催中・招集中の句会一覧を表示します")
    async def kukai_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_session() as session:
            kukais = await kukai_service.list_kukais(session, interaction.guild.id)

        if not kukais:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="現在、開催中または招集中の句会はありません。",
                    color=COLOR_INFO,
                ),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="📜 句会一覧", color=COLOR_INFO)
        for k in kukais[:10]:
            state_ja = STATE_LABEL.get(k.state, k.state)
            lines = [f"状態: {state_ja}"]
            if k.submission_close_at:
                lines.append(f"投句締切: {format_jst(k.submission_close_at)}")
            if k.selecting_close_at:
                lines.append(f"選句締切: {format_jst(k.selecting_close_at)}")
            embed.add_field(
                name=f"[{k.id}] {k.title}",
                value="\n".join(lines),
                inline=False,
            )
        if len(kukais) > 10:
            embed.set_footer(text=f"他 {len(kukais) - 10} 件")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @kukai.command(name="info", description="句会の詳細を表示します")
    @app_commands.describe(kukai_id="句会ID")
    async def kukai_info(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
            embed = _build_info_embed(kukai)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------

    @kukai.command(name="create", description="新しい句会を作成します（ウィザード形式）")
    async def kukai_create(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_session() as session:
            allowed = await permission_service.can_create_kukai(
                session, interaction.guild.id, interaction.user  # type: ignore[arg-type]
            )
            templates = await select_rule_service.list_templates(session, interaction.guild.id)
        if not allowed:
            await interaction.response.send_message(
                embed=error_embed("句会の作成権限がありません。"), ephemeral=True
            )
            return

        from bot.ui.wizard.base import goto_step
        from bot.ui.wizard.wizard_state import WizardState, set_wizard

        state = WizardState(user_id=interaction.user.id, guild_id=interaction.guild.id)
        state.select_preset_options = [{"id": t.id, "name": t.name} for t in templates]
        state.select_label_specs = select_rule_service.default_kukai_specs()
        state.selected_select_label = "特選"
        set_wizard(state)
        await goto_step(interaction, state, first_send=True)

    @kukai.command(name="proceed", description="【管理者】句会を次の状態へ進めます")
    @app_commands.describe(kukai_id="句会ID")
    async def kukai_proceed(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            published_count: int | None = None
            publish_warning: str | None = None
            result_count: int | None = None
            result_warning: str | None = None
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                current_state = KukaiState.from_value(kukai.state)
                if current_state in {KukaiState.SUBMISSION_CLOSED, KukaiState.WAITING_PUBLISH}:
                    await kukai_service.jump(session, kukai, KukaiState.WAITING_PUBLISH)
                    published = await submission_service.publish(session, kukai)
                    published_count = len(published)
                    publish_warning, message_id = await self._post_submission_list(
                        interaction.guild, kukai, published
                    )
                    if message_id is not None:
                        kukai.submission_message_id = message_id
                    new_state = await kukai_service.proceed(session, kukai)
                else:
                    new_state = await kukai_service.proceed(session, kukai)
                    if new_state == KukaiState.RESULTS:
                        result_count, result_warning, result_message_id = await self._post_result_list(
                            session, interaction.guild, kukai
                        )
                        if result_message_id is not None:
                            kukai.result_message_id = result_message_id
            state_ja = STATE_LABEL.get(str(new_state), str(new_state))
            description = f"句会「{kukai.title}」を **{state_ja}** へ進めました。"
            if published_count is not None:
                description += f"\n{published_count}句を番号付きで公開しました。"
                if publish_warning:
                    description += f"\n⚠️ {publish_warning}"
            if result_count is not None:
                description += f"\n結果 {result_count}句を公開しました。"
                if result_warning:
                    description += f"\n⚠️ {result_warning}"
            await interaction.response.send_message(
                embed=success_embed(description),
                ephemeral=True,
            )
            await self._announce_to_kukai_channel(interaction.guild, kukai, new_state)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="pause", description="【管理者】句会を一時停止します")
    @app_commands.describe(kukai_id="句会ID")
    async def kukai_pause(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                await kukai_service.pause(session, kukai)
                await notification_service.cancel_kukai_jobs(session, kukai.id)
            await interaction.response.send_message(
                embed=success_embed(f"句会「{kukai.title}」を一時停止しました。"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="resume", description="【管理者】句会を再開します")
    @app_commands.describe(kukai_id="句会ID")
    async def kukai_resume(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                restored = await kukai_service.resume(session, kukai)
                await notification_service.schedule_kukai_jobs(session, kukai)
            state_ja = STATE_LABEL.get(str(restored), str(restored))
            await interaction.response.send_message(
                embed=success_embed(f"句会「{kukai.title}」を再開しました。\n状態: **{state_ja}**"),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="cancel", description="【管理者】句会を中止します")
    @app_commands.describe(kukai_id="句会ID")
    async def kukai_cancel(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return

            view = ConfirmView()
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⚠️ 句会の中止",
                    description=f"句会「**{kukai.title}**」を中止します。\nこの操作は取り消せません。",
                    color=discord.Color.orange(),
                ),
                view=view,
                ephemeral=True,
            )
            await view.wait()
            if not view.confirmed:
                await interaction.edit_original_response(
                    embed=discord.Embed(description="キャンセルしました。", color=COLOR_INFO),
                    view=None,
                )
                return

            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                await kukai_service.cancel(session, kukai)
                await notification_service.cancel_kukai_jobs(session, kukai.id)

            await interaction.edit_original_response(
                embed=success_embed(f"句会「{kukai.title}」を中止しました。"),
                view=None,
            )
        except ServiceError as e:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=error_embed(str(e)), view=None)
            else:
                await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    async def _post_submission_list(
        self,
        guild: discord.Guild,
        kukai,
        published_submissions,
    ) -> tuple[str | None, int | None]:
        if not kukai.channel_id:
            return "公開先チャンネルが未設定のため、投句一覧を投稿できません。", None

        channel = guild.get_channel(kukai.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return "公開先チャンネルが見つからないため、投句一覧を投稿できません。", None

        embeds = build_submission_publish_embeds(kukai, published_submissions)
        first_message_id: int | None = None
        try:
            for index, embed in enumerate(embeds):
                sent = await send_with_retry(lambda e=embed: channel.send(embed=e))
                if index == 0:
                    first_message_id = sent.id
                if index < len(embeds) - 1:
                    await asyncio.sleep(0.35)
        except discord.Forbidden:
            return "公開チャンネルへの送信権限がないため、投句一覧を投稿できません。", None

        return None, first_message_id

    @staticmethod
    def _state_stage_label(state: KukaiState) -> str | None:
        mapping = {
            KukaiState.ENTRY_OPEN: "エントリー受付",
            KukaiState.SUBMISSION_OPEN: "投句受付",
            KukaiState.SELECTING_OPEN: "選句受付",
            KukaiState.RESULTS: "結果公開",
        }
        return mapping.get(state)

    async def _announce_to_kukai_channel(self, guild: discord.Guild, kukai, state: KukaiState) -> None:
        stage = self._state_stage_label(state)
        if not stage:
            return
        if not kukai.channel_id:
            return
        channel = guild.get_channel(kukai.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        description = f"句会「**{kukai.title}**」の **{stage}** を開始しました。"
        embed = discord.Embed(description=description, color=COLOR_INFO)
        if state == KukaiState.ENTRY_OPEN and kukai.entry_enabled and kukai.entry_close_at:
            embed.add_field(name="エントリー締切", value=format_jst(kukai.entry_close_at), inline=False)
        elif state == KukaiState.SUBMISSION_OPEN and kukai.submission_close_at:
            embed.add_field(name="投句締切", value=format_jst(kukai.submission_close_at), inline=False)
        elif state == KukaiState.SELECTING_OPEN and kukai.selecting_close_at:
            embed.add_field(name="選句締切", value=format_jst(kukai.selecting_close_at), inline=False)
        embed.set_footer(text=f"句会ID: {kukai.id}")
        view = StageActionView(kukai.id, state)
        try:
            await send_with_retry(lambda: channel.send(embed=embed, view=view))
        except Exception:
            pass

    async def _announce_settings_updated(self, guild: discord.Guild, kukai, *, deadlines_changed: bool) -> None:
        if not kukai.channel_id:
            return
        channel = guild.get_channel(kukai.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title="⚙️ 句会設定を更新しました",
            description=f"句会「**{kukai.title}**」の設定が更新されました。",
            color=COLOR_INFO,
        )
        if kukai.entry_enabled and kukai.entry_close_at:
            embed.add_field(name="エントリー締切", value=format_jst(kukai.entry_close_at), inline=False)
        if kukai.submission_close_at:
            embed.add_field(name="投句締切", value=format_jst(kukai.submission_close_at), inline=False)
        if kukai.selecting_close_at:
            embed.add_field(name="選句締切", value=format_jst(kukai.selecting_close_at), inline=False)
        if deadlines_changed:
            embed.set_footer(text="締切変更に合わせて通知ジョブを再登録済み")
        try:
            await send_with_retry(lambda: channel.send(embed=embed))
        except Exception:
            pass

    async def _post_result_list(
        self,
        session,
        guild: discord.Guild,
        kukai,
    ) -> tuple[int | None, str | None, int | None]:
        if not kukai.channel_id:
            return None, "公開先チャンネルが未設定のため、結果を投稿できません。", None

        channel = guild.get_channel(kukai.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None, "公開先チャンネルが見つからないため、結果を投稿できません。", None

        results = await result_service.compute_results(session, kukai)
        if not results:
            return 0, "集計対象の投句がないため、結果投稿をスキップしました。", None
        overall_comments = await select_repo.list_overall_comments(session, kukai.id)
        embeds = build_result_publish_embeds(kukai, results, overall_comments, guild)

        first_message_id: int | None = None
        try:
            for index, embed in enumerate(embeds):
                sent = await send_with_retry(lambda e=embed: channel.send(embed=e))
                if index == 0:
                    first_message_id = sent.id
                if index < len(embeds) - 1:
                    await asyncio.sleep(0.35)
        except discord.Forbidden:
            return len(results), "公開チャンネルへの送信権限がないため、結果を投稿できません。", None

        return len(results), None, first_message_id

    @kukai.command(name="rollback", description="【管理者】投句公開を取り消し、公開待ちに戻します")
    @app_commands.describe(kukai_id="句会ID")
    async def kukai_rollback(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                if KukaiState.from_value(kukai.state) not in {
                    KukaiState.WAITING_PUBLISH,
                    KukaiState.SELECTING_OPEN,
                    KukaiState.SELECTING_CLOSED,
                }:
                    await interaction.response.send_message(
                        embed=error_embed("この状態ではロールバックできません。"), ephemeral=True
                    )
                    return

            view = RollbackView()
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⚠️ 投句公開のロールバック",
                    description=(
                        f"句会「**{kukai.title}**」の投句公開を取り消します。\n"
                        "投句番号割当が削除され、状態が「投句公開待ち」に戻ります。\n\n"
                        "選句中の場合、既存の選句を保持するか選んでください。"
                    ),
                    color=discord.Color.orange(),
                ),
                view=view,
                ephemeral=True,
            )
            await view.wait()

            if view.choice is None:
                await interaction.edit_original_response(
                    embed=discord.Embed(description="キャンセルしました。", color=COLOR_INFO),
                    view=None,
                )
                return

            reset_selects = view.choice == "reset_selects"
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                await submission_service.rollback_publish(session, kukai, reset_selects=reset_selects)
                await kukai_service.jump(session, kukai, KukaiState.WAITING_PUBLISH)

            extra = "（選句もリセット）" if reset_selects else "（選句は保持）"
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"ロールバックしました。{extra}\n状態: **投句公開待ち**",
                    color=COLOR_SUCCESS,
                ),
                view=None,
            )
        except ServiceError as e:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=error_embed(str(e)), view=None)
            else:
                await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="edit", description="【管理者】句会の設定を変更します")
    @app_commands.describe(
        kukai_id="句会ID",
        title="新しいタイトル",
        theme="新しい題（空文字でクリア）",
        description="新しい説明（空文字でクリア）",
        submission_close_at="投句締切 (例: 2026-05-20 23:59 JST)",
        selecting_close_at="選句締切 (例: 2026-05-21 23:59 JST)",
        submission_min="最小投句数",
        submission_max="最大投句数",
        submission_max_unlimited="最大投句数を無制限にする",
        submission_mode="投句進行モード",
        selecting_mode="選句進行モード",
        publish_mode="投句公開モード",
        result_mode="結果公開モード",
        author_reveal="作者公開するか",
        author_reveal_zero="0点以下作者を公開するか",
    )
    async def kukai_edit(
        self,
        interaction: discord.Interaction,
        kukai_id: int,
        title: str | None = None,
        theme: str | None = None,
        description: str | None = None,
        submission_close_at: str | None = None,
        selecting_close_at: str | None = None,
        submission_min: int | None = None,
        submission_max: int | None = None,
        submission_max_unlimited: bool | None = None,
        submission_mode: Literal["manual", "semi_auto", "full_auto"] | None = None,
        selecting_mode: Literal["manual", "semi_auto", "full_auto"] | None = None,
        publish_mode: Literal["manual", "auto"] | None = None,
        result_mode: Literal["manual", "auto"] | None = None,
        author_reveal: bool | None = None,
        author_reveal_zero: bool | None = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            submission_close_dt = parse_datetime(submission_close_at) if submission_close_at else None
            selecting_close_dt = parse_datetime(selecting_close_at) if selecting_close_at else None
        except ValueError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return

                deadlines_changed = await kukai_service.edit_kukai(
                    session,
                    kukai,
                    title=title,
                    theme=theme,
                    description=description,
                    submission_close_at=submission_close_dt,
                    selecting_close_at=selecting_close_dt,
                    submission_min=submission_min,
                    submission_max=submission_max,
                    submission_max_unlimited=bool(submission_max_unlimited),
                    submission_mode=submission_mode,
                    selecting_mode=selecting_mode,
                    publish_mode=publish_mode,
                    result_mode=result_mode,
                    author_reveal=author_reveal,
                    author_reveal_zero=author_reveal_zero,
                )

                if deadlines_changed:
                    await notification_service.cancel_kukai_jobs(session, kukai.id)
                    await notification_service.schedule_kukai_jobs(session, kukai)

            extra = "\n締切変更に合わせて通知ジョブを再登録しました。" if deadlines_changed else ""
            await interaction.response.send_message(
                embed=success_embed(f"句会「{kukai.title}」の設定を更新しました。{extra}"),
                ephemeral=True,
            )
            await self._announce_settings_updated(interaction.guild, kukai, deadlines_changed=deadlines_changed)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)


def _build_info_embed(kukai) -> discord.Embed:
    state_ja = STATE_LABEL.get(kukai.state, kukai.state)
    embed = discord.Embed(
        title=f"📋 {kukai.title}",
        description=kukai.description or "",
        color=COLOR_RESULT if kukai.state == "results" else COLOR_INFO,
    )
    if kukai.theme:
        embed.add_field(name="題", value=kukai.theme, inline=True)
    embed.add_field(name="状態", value=state_ja, inline=True)
    if kukai.submission_close_at:
        embed.add_field(name="投句締切", value=format_jst(kukai.submission_close_at), inline=False)
    if kukai.selecting_close_at:
        embed.add_field(name="選句締切", value=format_jst(kukai.selecting_close_at), inline=False)
    embed.set_footer(text=f"句会ID: {kukai.id}")
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(KukaiCog(bot))
