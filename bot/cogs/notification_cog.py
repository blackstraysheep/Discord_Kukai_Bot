"""Notification management commands: /notification *"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from bot.database import get_session
from bot.models.notification import NotificationSchedule
from bot.models.voice_session import VoiceSession
from bot.services import kukai_service, notification_service, permission_service
from bot.services.errors import ServiceError
from bot.utils.bulk_parser import BulkParseError, parse_reminder_spec
from bot.utils.embed_builder import COLOR_INFO, error_embed, success_embed


_EVENT_LABELS = {
    "entry_close": "エントリー締切",
    "submission_close": "投句締切",
    "selecting_close": "選句締切",
    "voice_start": "ボイス句会開始",
}


def _format_offset(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _format_destination(channel_id: int | None, mention: bool) -> str:
    if channel_id == -1:
        return "DM"
    base = "句会チャンネル" if channel_id is None else f"<#{channel_id}>"
    if mention:
        base += " + mention"
    return base


class NotificationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    notification = app_commands.Group(name="notification", description="句会通知設定の管理")

    @notification.command(name="list", description="句会の通知設定を表示します")
    @app_commands.describe(kukai_id="句会ID")
    async def notification_list(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                schedules = (
                    await session.execute(
                        select(NotificationSchedule)
                        .where(NotificationSchedule.kukai_id == kukai.id)
                        .order_by(
                            NotificationSchedule.event_type,
                            NotificationSchedule.offset_secs.desc(),
                            NotificationSchedule.id,
                        )
                    )
                ).scalars().all()
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        embed = discord.Embed(title=f"通知設定 — {kukai.title}", color=COLOR_INFO)
        if not schedules:
            embed.description = "未登録です。ジョブ登録時にデフォルト通知が作成されます。"
        else:
            lines = []
            for row in schedules:
                fired = " / 送信済み" if row.fired else ""
                lines.append(
                    f"[{row.id}] {_EVENT_LABELS.get(row.event_type, row.event_type)} "
                    f"{_format_offset(row.offset_secs)}前 / "
                    f"{_format_destination(row.channel_id, row.mention)} / "
                    f"{row.target}{fired}"
                )
            embed.description = "\n".join(lines[:20])
            if len(schedules) > 20:
                embed.set_footer(text=f"他 {len(schedules) - 20} 件")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @notification.command(name="set", description="【管理者】通知設定を一括で差し替えます")
    @app_commands.describe(
        kukai_id="句会ID",
        config="1行1件: event,offset,destination,target,mention",
    )
    async def notification_set(
        self,
        interaction: discord.Interaction,
        kukai_id: int,
        config: str,
    ) -> None:
        try:
            specs = []
            for line_no, raw in enumerate(config.splitlines(), start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                specs.append(parse_reminder_spec(line, line_no=line_no))
            if not specs:
                raise BulkParseError("通知設定を1件以上入力してください。")
        except BulkParseError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.edit_original_response(
                        embed=error_embed("この操作は句会管理者のみ実行できます。")
                    )
                    return
                voice_session = (
                    await session.execute(select(VoiceSession).where(VoiceSession.kukai_id == kukai.id))
                ).scalar_one_or_none()
                kukai.__dict__["voice_session"] = voice_session
                await notification_service.cancel_kukai_jobs(session, kukai.id)
                await notification_service.replace_notification_schedules(session, kukai, specs)
                await notification_service.schedule_kukai_jobs(session, kukai)
        except ServiceError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)))
            return

        await interaction.edit_original_response(
            embed=success_embed(f"句会 `{kukai_id}` の通知設定を {len(specs)} 件に差し替えました。"),
        )

    @notification.command(name="reset", description="【管理者】通知設定をデフォルトに戻します")
    @app_commands.describe(kukai_id="句会ID")
    async def notification_reset(self, interaction: discord.Interaction, kukai_id: int) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.edit_original_response(
                        embed=error_embed("この操作は句会管理者のみ実行できます。")
                    )
                    return
                voice_session = (
                    await session.execute(select(VoiceSession).where(VoiceSession.kukai_id == kukai.id))
                ).scalar_one_or_none()
                kukai.__dict__["voice_session"] = voice_session
                await notification_service.cancel_kukai_jobs(session, kukai.id)
                await notification_service.replace_notification_schedules(session, kukai, [])
                await notification_service.schedule_kukai_jobs(session, kukai)
        except ServiceError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)))
            return

        await interaction.edit_original_response(
            embed=success_embed(f"句会 `{kukai_id}` の通知設定をデフォルトに戻しました。"),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NotificationCog(bot))
