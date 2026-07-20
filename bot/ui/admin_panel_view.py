"""Kukai admin panel views."""

from __future__ import annotations

import discord
from sqlalchemy import select

from bot.database import get_session
from bot.models.notification import NotificationSchedule
from bot.services import (
    author_publication_service,
    kukai_service,
    notification_service,
    pdf_delivery_service,
    pdf_service,
    permission_service,
    proceed_service,
    result_service,
)
from bot.services.errors import ServiceError
from bot.services.pdf_service import PdfError
from bot.state_machine.states import KukaiState
from bot.ui.common import ConfirmView
from bot.utils.channel import effective_channel_id
from bot.utils.datetime_utils import format_jst
from bot.utils.embed_builder import COLOR_INFO, error_embed, success_embed
from bot.utils.stage_announcement import send_action_button_message, send_stage_announcement


def admin_panel_entry_custom_id(kukai_id: int) -> str:
    return f"kukai:admin-panel:{kukai_id}"


class KukaiAdminPanelEntryView(discord.ui.View):
    def __init__(self, kukai_id: int) -> None:
        super().__init__(timeout=None)
        self.kukai_id = kukai_id
        button = discord.ui.Button(
            label="管理パネルを開く",
            style=discord.ButtonStyle.primary,
            custom_id=admin_panel_entry_custom_id(kukai_id),
        )

        async def _callback(interaction: discord.Interaction) -> None:
            assert interaction.guild is not None
            try:
                async with get_session() as session:
                    kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                    if not await permission_service.is_kukai_admin(
                        session,
                        kukai,
                        interaction.user,  # type: ignore[arg-type]
                    ):
                        await interaction.response.send_message(
                            embed=error_embed("この句会の管理者権限がありません。"),
                            ephemeral=True,
                        )
                        return
                    embed = await build_admin_panel_embed(session, kukai)
                    view = await build_admin_panel_view(
                        session,
                        kukai,
                        user_id=interaction.user.id,
                    )
                await interaction.response.send_message(
                    embed=embed,
                    view=view,
                    ephemeral=True,
                )
            except ServiceError as error:
                await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)

        button.callback = _callback
        self.add_item(button)


class KukaiAdminPanelView(discord.ui.View):
    def __init__(
        self,
        *,
        kukai_id: int,
        user_id: int,
        state: KukaiState,
        author_publication_mode: str,
        author_reveal: bool,
        has_published_submissions: bool,
    ) -> None:
        super().__init__(timeout=900)
        self.kukai_id = kukai_id
        self.user_id = user_id
        self._add_export_buttons(
            state=state,
            author_publication_mode=author_publication_mode,
            author_reveal=author_reveal,
            has_published_submissions=has_published_submissions,
        )

    def _add_export_buttons(
        self,
        *,
        state: KukaiState,
        author_publication_mode: str,
        author_reveal: bool,
        has_published_submissions: bool,
    ) -> None:
        submission_button = discord.ui.Button(
            label="投句一覧PDFを送信",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=not has_published_submissions,
        )

        async def submission_callback(interaction: discord.Interaction) -> None:
            await self._open_pdf_send(interaction, "submission")

        submission_button.callback = submission_callback
        self.add_item(submission_button)

        result_button = discord.ui.Button(
            label="結果PDFを送信",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=state not in pdf_delivery_service.PUBLIC_RESULT_STATES,
        )

        async def result_callback(interaction: discord.Interaction) -> None:
            await self._open_pdf_send(interaction, "result")

        result_button.callback = result_callback
        self.add_item(result_button)

        if author_publication_mode == "manual":
            author_button = discord.ui.Button(
                label="作者公開済み" if author_reveal else "作者を公開",
                style=discord.ButtonStyle.success,
                row=1,
                disabled=author_reveal or state not in pdf_delivery_service.AUTHOR_VISIBLE_STATES,
            )

            async def author_callback(interaction: discord.Interaction) -> None:
                await self._confirm_reveal_authors(interaction)

            author_button.callback = author_callback
            self.add_item(author_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            embed=error_embed("この管理パネルは開いた本人だけが操作できます。"),
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="状態更新", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._refresh(interaction)

    @discord.ui.button(label="次へ進める", style=discord.ButtonStyle.primary)
    async def proceed(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await self._load_authorized(session, interaction)
                preview = await proceed_service.preview_proceed(session, kukai)
                title = kukai.title
                kukai_id = kukai.id

            view = ConfirmView(timeout=60)
            if view.children:
                view.children[0].label = "次へ進める"  # type: ignore[attr-defined]
            await interaction.response.send_message(
                embed=build_proceed_preview_embed(kukai_id=kukai_id, title=title, preview=preview),
                view=view,
                ephemeral=True,
            )
            await view.wait()
            if not view.confirmed:
                await interaction.edit_original_response(
                    embed=discord.Embed(description="進行をキャンセルしました。", color=COLOR_INFO),
                    view=None,
                )
                return

            async with get_session() as session:
                kukai = await self._load_authorized(session, interaction)
                result = await proceed_service.execute_proceed(
                    bot=interaction.client,  # type: ignore[arg-type]
                    session=session,
                    guild=interaction.guild,
                    kukai=kukai,
                    actor_user_id=interaction.user.id,
                    source_label="管理パネル",
                    interaction_id=getattr(interaction, "id", None),
                    channel_id=effective_channel_id(interaction),
                    allow_incomplete=True,
                )
            await interaction.edit_original_response(
                embed=success_embed(result.success_description()),
                view=None,
            )
            await proceed_service.announce_proceed_result(interaction.guild, kukai, result.after_state)
        except ServiceError as error:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=error_embed(str(error)), view=None)
            else:
                await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)
        except proceed_service.ProceedNeedsConfirmation as error:
            if interaction.response.is_done():
                await interaction.edit_original_response(
                    embed=build_proceed_preview_embed(kukai_id=self.kukai_id, title="句会", preview=error.preview),
                    view=None,
                )
            else:
                await interaction.response.send_message(
                    embed=build_proceed_preview_embed(kukai_id=self.kukai_id, title="句会", preview=error.preview),
                    ephemeral=True,
                )
    @discord.ui.button(label="一時停止", style=discord.ButtonStyle.secondary)
    async def pause(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._change_state(interaction, "pause")

    @discord.ui.button(label="再開", style=discord.ButtonStyle.secondary)
    async def resume(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._change_state(interaction, "resume")

    @discord.ui.button(label="中止", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await self._load_authorized(session, interaction)
            view = ConfirmView()
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="句会の中止",
                    description=f"句会「**{kukai.title}**」を中止します。この操作は取り消せません。",
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
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                await kukai_service.cancel(session, kukai)
                await notification_service.cancel_kukai_jobs(session, kukai.id)
            await interaction.edit_original_response(
                embed=success_embed(f"句会「{kukai.title}」を中止しました。"),
                view=None,
            )
            await send_stage_announcement(interaction.guild, kukai, KukaiState.CANCELLED)
        except ServiceError as error:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=error_embed(str(error)), view=None)
            else:
                await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)

    @discord.ui.button(label="操作ボタン再投稿", style=discord.ButtonStyle.secondary, row=1)
    async def repost_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            result_count: int | None = None
            async with get_session() as session:
                kukai = await self._load_authorized(session, interaction)
                if KukaiState.from_value(kukai.state) in {KukaiState.RESULTS, KukaiState.ENDED}:
                    result_count = len(await result_service.compute_results(session, kukai))
            if not kukai.channel_id:
                await interaction.edit_original_response(embed=error_embed("開催チャンネルが未設定です。"))
                return
            channel = interaction.guild.get_channel(kukai.channel_id)
            if channel is None or not hasattr(channel, "send"):
                await interaction.edit_original_response(embed=error_embed("開催チャンネルが見つかりません。"))
                return
            error = await send_action_button_message(channel, kukai, "current", result_count=result_count)
            if error:
                await interaction.edit_original_response(embed=error_embed(error))
                return
            await interaction.edit_original_response(embed=success_embed("現在の状態に合わせた操作ボタンを再投稿しました。"))
        except ServiceError as error:
            await interaction.edit_original_response(embed=error_embed(str(error)))

    async def _open_pdf_send(
        self,
        interaction: discord.Interaction,
        kind: pdf_delivery_service.PdfKind,
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await self._load_authorized(session, interaction)
                state = KukaiState.from_value(kukai.state)
                if kind == "submission":
                    if not await pdf_delivery_service.has_published_submissions(session, kukai.id):
                        raise ServiceError("投句一覧がまだ公開されていません。")
                elif state not in pdf_delivery_service.PUBLIC_RESULT_STATES:
                    raise ServiceError("結果PDFは結果公開後に開催チャンネルへ送信できます。")
                if not kukai.channel_id:
                    raise ServiceError("開催チャンネルが未設定です。")
                can_named = pdf_delivery_service.can_show_author(kukai, True, state=state)
                channel_id = kukai.channel_id
                title = kukai.title
            view = PdfSendConfirmView(
                kukai_id=self.kukai_id,
                user_id=self.user_id,
                kind=kind,
                can_named=can_named,
            )
            kind_label = "投句一覧PDF" if kind == "submission" else "結果PDF"
            embed = discord.Embed(
                title=f"{kind_label}を送信",
                description=(
                    f"句会「**{title}**」\n"
                    f"送信先: <#{channel_id}>\n"
                    "記名状態を選び、送信を確定してください。"
                ),
                color=COLOR_INFO,
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except ServiceError as error:
            await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)

    async def _confirm_reveal_authors(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        panel_message = interaction.message
        view = ConfirmView(timeout=60)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="作者を公開",
                description="作者名を公開します。この操作は取り消せません。",
                color=discord.Color.orange(),
            ),
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            await interaction.edit_original_response(
                embed=discord.Embed(description="作者公開をキャンセルしました。", color=COLOR_INFO),
                view=None,
            )
            return
        try:
            async with get_session() as session:
                kukai = await self._load_authorized(session, interaction)
                if getattr(kukai, "author_publication_mode", "with_result") != "manual":
                    raise ServiceError("作者公開設定が手動公開ではありません。")
                if not author_publication_service.reveal_authors(kukai):
                    raise ServiceError("作者はすでに公開されています。")
                title = kukai.title
            await author_publication_service.announce_authors_revealed(interaction.guild, kukai)
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                panel_embed = await build_admin_panel_embed(session, kukai)
                panel_view = await build_admin_panel_view(session, kukai, user_id=self.user_id)
            if panel_message is not None:
                await panel_message.edit(embed=panel_embed, view=panel_view)
            await interaction.edit_original_response(
                embed=success_embed(f"句会「{title}」の作者を公開しました。"),
                view=None,
            )
        except ServiceError as error:
            await interaction.edit_original_response(embed=error_embed(str(error)), view=None)

    async def _change_state(self, interaction: discord.Interaction, action: str) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await self._load_authorized(session, interaction)
                if action == "pause":
                    await kukai_service.pause(session, kukai)
                    await notification_service.cancel_kukai_jobs(session, kukai.id)
                    state = KukaiState.PAUSED
                    message = f"句会「{kukai.title}」を一時停止しました。"
                else:
                    state = await kukai_service.resume(session, kukai)
                    await notification_service.schedule_kukai_jobs(session, kukai)
                    state_label = state.value if isinstance(state, KukaiState) else str(state)
                    message = f"句会「{kukai.title}」を再開しました。\n状態: **{state_label}**"
            await interaction.response.send_message(embed=success_embed(message), ephemeral=True)
            await send_stage_announcement(interaction.guild, kukai, state)
        except ServiceError as error:
            await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await self._load_authorized(session, interaction)
                embed = await build_admin_panel_embed(session, kukai)
                view = await build_admin_panel_view(session, kukai, user_id=self.user_id)
            await interaction.response.edit_message(embed=embed, view=view)
        except ServiceError as error:
            await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)

    async def _load_authorized(self, session, interaction: discord.Interaction):
        assert interaction.guild is not None
        kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
        if not await permission_service.is_kukai_admin(
            session,
            kukai,
            interaction.user,  # type: ignore[arg-type]
        ):
            raise ServiceError("この句会の管理者権限がありません。")
        return kukai


class PdfSendConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        kukai_id: int,
        user_id: int,
        kind: pdf_delivery_service.PdfKind,
        can_named: bool,
    ) -> None:
        super().__init__(timeout=120)
        self.kukai_id = kukai_id
        self.user_id = user_id
        self.kind = kind
        self.show_author = False
        options = [
            discord.SelectOption(label="無記名", value="anonymous", default=True),
        ]
        if can_named:
            options.append(discord.SelectOption(label="記名", value="named"))
        identity_select = discord.ui.Select(
            placeholder="記名状態",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

        async def identity_callback(interaction: discord.Interaction) -> None:
            self.show_author = identity_select.values[0] == "named"
            await interaction.response.defer()

        identity_select.callback = identity_callback
        self.add_item(identity_select)

        send_button = discord.ui.Button(label="開催チャンネルへ送信", style=discord.ButtonStyle.primary, row=1)

        async def send_callback(interaction: discord.Interaction) -> None:
            await self._send(interaction)

        send_button.callback = send_callback
        self.add_item(send_button)

        cancel_button = discord.ui.Button(label="キャンセル", style=discord.ButtonStyle.secondary, row=1)

        async def cancel_callback(interaction: discord.Interaction) -> None:
            self.stop()
            await interaction.response.edit_message(
                embed=discord.Embed(description="PDF送信をキャンセルしました。", color=COLOR_INFO),
                view=None,
            )

        cancel_button.callback = cancel_callback
        self.add_item(cancel_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            embed=error_embed("この確認画面は開いた本人だけが操作できます。"),
            ephemeral=True,
        )
        return False

    async def _send(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if not pdf_service.is_available():
            await interaction.response.send_message(
                embed=error_embed("この環境ではPDF生成が有効化されていません（LUALATEX_BIN未設定）。"),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    raise ServiceError("この句会の管理者権限がありません。")
                state = KukaiState.from_value(kukai.state)
                if self.kind == "result" and state not in pdf_delivery_service.PUBLIC_RESULT_STATES:
                    raise ServiceError("結果PDFは結果公開後に開催チャンネルへ送信できます。")
                if not kukai.channel_id:
                    raise ServiceError("開催チャンネルが未設定です。")
                pdf_bytes, filename = await pdf_delivery_service.build_pdf(
                    session,
                    kukai,
                    interaction.guild,
                    kind=self.kind,
                    show_author=self.show_author,
                )
                channel_id = kukai.channel_id
                title = kukai.title
            channel = interaction.guild.get_channel(channel_id)
            if channel is None or not hasattr(channel, "send"):
                raise ServiceError("開催チャンネルが見つかりません。")
            await pdf_delivery_service.send_pdf_to_channel(
                channel,
                pdf_bytes=pdf_bytes,
                filename=filename,
                kukai_id=self.kukai_id,
            )
            self.stop()
            await interaction.edit_original_response(
                embed=success_embed(f"句会「{title}」のPDFを開催チャンネルへ送信しました。"),
                view=None,
            )
        except (PdfError, ServiceError) as error:
            await interaction.edit_original_response(embed=error_embed(str(error)), view=None)
        except discord.HTTPException:
            await interaction.edit_original_response(
                embed=error_embed("開催チャンネルへのPDF送信に失敗しました。"),
                view=None,
            )


async def build_admin_panel_view(session, kukai, *, user_id: int) -> KukaiAdminPanelView:
    return KukaiAdminPanelView(
        kukai_id=kukai.id,
        user_id=user_id,
        state=KukaiState.from_value(kukai.state),
        author_publication_mode=getattr(kukai, "author_publication_mode", "with_result"),
        author_reveal=bool(getattr(kukai, "author_reveal", False)),
        has_published_submissions=await pdf_delivery_service.has_published_submissions(
            session,
            kukai.id,
        ),
    )


async def build_admin_panel_embed(session, kukai) -> discord.Embed:
    result = await session.execute(
        select(NotificationSchedule).where(NotificationSchedule.kukai_id == kukai.id)
    )
    notification_count = len(list(result.scalars().all()))
    embed = discord.Embed(title=f"管理パネル - {kukai.title}", color=COLOR_INFO)
    embed.add_field(name="句会ID", value=str(kukai.id), inline=True)
    embed.add_field(name="現在状態", value=kukai.state, inline=True)
    if kukai.channel_id:
        embed.add_field(name="開催チャンネル", value=f"<#{kukai.channel_id}>", inline=False)
    deadlines = []
    if kukai.entry_close_at:
        deadlines.append(f"エントリー: {format_jst(kukai.entry_close_at)}")
    if kukai.submission_close_at:
        deadlines.append(f"投句: {format_jst(kukai.submission_close_at)}")
    if kukai.selecting_close_at:
        deadlines.append(f"選句: {format_jst(kukai.selecting_close_at)}")
    embed.add_field(name="締切", value="\n".join(deadlines) if deadlines else "未設定", inline=False)
    embed.add_field(
        name="進行モード",
        value=f"投句: {kukai.submission_mode}\n選句: {kukai.selecting_mode}",
        inline=True,
    )
    embed.add_field(
        name="作者公開",
        value=f"{kukai.author_publication_mode} / {'公開済み' if kukai.author_reveal else '未公開'}",
        inline=True,
    )
    embed.add_field(name="通知設定", value=f"{notification_count}件", inline=True)
    return embed


def build_proceed_preview_embed(
    *,
    kukai_id: int,
    title: str,
    preview: proceed_service.ProceedPreview,
) -> discord.Embed:
    embed = discord.Embed(
        title="次へ進める",
        description=f"句会「**{title}**」を進行します。",
        color=discord.Color.orange() if preview.has_incomplete else COLOR_INFO,
    )
    embed.add_field(
        name="状態",
        value=(
            f"現在: **{proceed_service.state_label(preview.current_state)}**\n"
            f"次: **{proceed_service.state_label(preview.next_state)}**"
        ),
        inline=False,
    )
    embed.add_field(name="実行される処理", value=_limited_field_value(list(preview.effects)), inline=False)
    if preview.progress_report is not None:
        report = preview.progress_report
        embed.add_field(
            name="条件確認",
            value=report.summary(),
            inline=False,
        )
        if not report.complete:
            embed.add_field(name="未達状況", value=_limited_field_value(report.admin_lines()), inline=False)
    embed.set_footer(text=f"句会ID: {kukai_id}")
    return embed


def build_admin_panel_entry_embed(kukai) -> discord.Embed:
    embed = discord.Embed(
        title="句会管理パネル",
        description=f"句会「**{kukai.title}**」の管理操作を開きます。",
        color=COLOR_INFO,
    )
    embed.set_footer(text=f"句会ID: {kukai.id}")
    return embed


def _limited_field_value(lines: list[str], *, limit: int = 1024) -> str:
    if not lines:
        return "（なし）"
    value = ""
    shown = 0
    for line in lines:
        candidate = f"{value}\n{line}" if value else line
        if len(candidate) > limit:
            remaining = len(lines) - shown
            suffix = f"\n...他 {remaining} 件"
            if value and len(value) + len(suffix) <= limit:
                value += suffix
            break
        value = candidate
        shown += 1
    return value or "（表示できる項目がありません）"
