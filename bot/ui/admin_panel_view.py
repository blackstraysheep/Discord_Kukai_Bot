"""Kukai admin panel views."""

from __future__ import annotations

import discord
from sqlalchemy import select

from bot.database import get_session
from bot.models.notification import NotificationSchedule
from bot.services import kukai_service, notification_service, permission_service, result_service
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.ui.common import ConfirmView
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
                await interaction.response.send_message(
                    embed=embed,
                    view=KukaiAdminPanelView(kukai_id=self.kukai_id, user_id=interaction.user.id),
                    ephemeral=True,
                )
            except ServiceError as error:
                await interaction.response.send_message(embed=error_embed(str(error)), ephemeral=True)

        button.callback = _callback
        self.add_item(button)


class KukaiAdminPanelView(discord.ui.View):
    def __init__(self, *, kukai_id: int, user_id: int) -> None:
        super().__init__(timeout=900)
        self.kukai_id = kukai_id
        self.user_id = user_id

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
    async def proceed_hint(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            embed=discord.Embed(
                description=(
                    "次へ進める操作は既存 `/kukai proceed` を使ってください。\n"
                    "条件未達確認、管理者スレッド記録、投稿・通知の副作用を既存経路に集約しています。"
                ),
                color=COLOR_INFO,
            ),
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
            await interaction.response.edit_message(embed=embed, view=self)
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


def build_admin_panel_entry_embed(kukai) -> discord.Embed:
    embed = discord.Embed(
        title="句会管理パネル",
        description=f"句会「**{kukai.title}**」の管理操作を開きます。",
        color=COLOR_INFO,
    )
    embed.set_footer(text=f"句会ID: {kukai.id}")
    return embed
