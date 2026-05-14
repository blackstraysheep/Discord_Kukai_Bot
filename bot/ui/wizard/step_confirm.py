"""Wizard step 6: Confirmation + kukai creation."""

from __future__ import annotations

import logging
import re

import discord

from bot.database import get_session
from bot.services import kukai_service, notification_service
from bot.services.errors import ServiceError
from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, clear_wizard
from bot.utils.datetime_utils import format_jst
from bot.utils.embed_builder import COLOR_INFO, COLOR_SUCCESS, error_embed

logger = logging.getLogger(__name__)


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title=f"ステップ 7/{STEP_COUNT}: 確認",
        description="以下の設定で句会を作成します。よろしければ「作成」を押してください。",
        color=discord.Color.green(),
    )
    embed.add_field(name="題名", value=state.title, inline=False)
    if state.theme:
        embed.add_field(name="題（お題）", value=state.theme, inline=True)
    if state.description:
        embed.add_field(name="説明", value=state.description[:200], inline=False)

    entry_str = format_jst(state.entry_close_at) if state.entry_close_at else "未設定"
    sub_str = format_jst(state.submission_close_at) if state.submission_close_at else "未設定"
    selecting_str = format_jst(state.selecting_close_at) if state.selecting_close_at else "未設定"
    embed.add_field(name="エントリー締切", value=entry_str, inline=True)
    embed.add_field(name="投句締切", value=sub_str, inline=True)
    embed.add_field(name="選句締切", value=selecting_str, inline=True)

    entry_str = "有効" if state.entry_enabled else "無効"
    if state.entry_enabled:
        entry_str += f"　承認: {'要' if state.entry_approval else '不要'}"
    embed.add_field(name="エントリー", value=entry_str, inline=True)

    sub_mode_labels = {"manual": "手動", "semi_auto": "半自動", "full_auto": "全自動"}
    max_label = "∞" if state.submission_max is None else str(state.submission_max)
    embed.add_field(
        name="投句設定",
        value=(
            f"投句モード: {sub_mode_labels.get(state.submission_mode, state.submission_mode)}"
            f"　選句モード: {sub_mode_labels.get(state.selecting_mode, state.selecting_mode)}"
            f"　{state.submission_min}〜{max_label}句"
        ),
        inline=False,
    )

    embed.add_field(
        name="進行・公開設定",
        value=(
            f"結果: {'自動' if state.result_mode == 'auto' else '手動'}"
            f"　作者: {'公開' if state.author_reveal else '非公開'}"
            f"　0点以下作者: {('公開' if state.author_reveal_zero else '非公開') if state.author_reveal else '適用外'}"
        ),
        inline=False,
    )
    channel_target = (
        f"既存チャンネル: <#{state.existing_channel_id}>"
        if state.use_existing_channel and state.existing_channel_id
        else f"新規チャンネルを作成（`{_sanitize_channel_name(state.channel_name or state.title)}`）"
    )
    embed.add_field(name="句会チャンネル", value=channel_target, inline=False)
    label_lines = []
    for spec in state.select_label_specs:
        max_count = "∞" if spec.get("max_count") is None else str(spec.get("max_count"))
        label_lines.append(
            f"{spec.get('label')} ({spec.get('point', 0):+d}pt / {spec.get('min_count', 0)}〜{max_count})"
        )
    embed.add_field(
        name="選句仕様",
        value=(
            f"プリセット: {state.select_preset_name}\n"
            + ("\n".join(label_lines[:8]) if label_lines else "未設定")
        ),
        inline=False,
    )
    return embed, StepConfirmView(state)


def _sanitize_channel_name(title: str) -> str:
    name = title.replace(" ", "-").replace("　", "-")
    name = re.sub(r'[<>"\'\\|]', "", name)
    name = name[:100].strip("-") or "kukai"
    return name


class StepConfirmView(discord.ui.View):
    def __init__(self, state: WizardState) -> None:
        super().__init__(timeout=900)
        self.state = state

        confirm_btn = discord.ui.Button(
            label="✅ 作成",
            style=discord.ButtonStyle.success,
            disabled=not state.can_confirm,
            row=0,
        )
        confirm_btn.callback = self._confirm
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(
            label="← 戻る", style=discord.ButtonStyle.secondary, row=0
        )
        back_btn.callback = self._back
        self.add_item(back_btn)

        cancel_btn = discord.ui.Button(
            label="❌ キャンセル", style=discord.ButtonStyle.danger, row=0
        )
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 6
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self.state
        state.result_mode = "manual" if state.selecting_mode == "manual" else "auto"
        guild = interaction.guild
        assert guild is not None

        ch_name = _sanitize_channel_name(state.channel_name or state.title)
        channel: discord.abc.GuildChannel | None = None
        created_new_channel = False
        name_collision_warning: str | None = None
        if state.use_existing_channel:
            if state.existing_channel_id is None:
                await interaction.edit_original_response(
                    embed=error_embed("既存チャンネルが未選択です。ステップ1で選択してください。"),
                    view=None,
                )
                return
            candidate = guild.get_channel(state.existing_channel_id)
            if not isinstance(candidate, discord.TextChannel):
                await interaction.edit_original_response(
                    embed=error_embed("選択されたチャンネルが見つからないか、テキストチャンネルではありません。"),
                    view=None,
                )
                return
            channel = candidate
        else:
            existing_same_name = [
                ch for ch in guild.text_channels if ch.name == ch_name
            ]
            if existing_same_name:
                name_collision_warning = (
                    f"⚠️ 同名のチャンネル「{ch_name}」が既に存在します（{len(existing_same_name)}件）。"
                    " 新しいチャンネルを別途作成しました。"
                )
            category: discord.CategoryChannel | None = None
            if state.category_id:
                cat = guild.get_channel(state.category_id)
                if isinstance(cat, discord.CategoryChannel):
                    category = cat
            try:
                channel = await guild.create_text_channel(ch_name, category=category)
                created_new_channel = True
            except discord.Forbidden:
                await interaction.edit_original_response(
                    embed=error_embed("チャンネルを作成する権限がありません。\nBotにチャンネル管理権限を付与してください。"),
                    view=None,
                )
                return

        async def _safe_delete_channel() -> None:
            if not created_new_channel or not isinstance(channel, discord.TextChannel):
                return
            try:
                await channel.delete()
            except Exception:
                logger.exception("Failed to delete temporary channel after wizard error")

        try:
            async with get_session() as session:
                kukai = await kukai_service.create_kukai(
                    session,
                    guild_id=guild.id,
                    created_by=interaction.user.id,
                    channel_id=channel.id,
                    title=state.title,
                    theme=state.theme or None,
                    description=state.description or None,
                    entry_close_at=state.entry_close_at,
                    submission_close_at=state.submission_close_at,
                    selecting_close_at=state.selecting_close_at,
                    entry_enabled=state.entry_enabled,
                    entry_approval=state.entry_approval,
                    min_participants=state.min_participants,
                    submission_min=state.submission_min,
                    submission_max=state.submission_max,
                    submission_mode=state.submission_mode,
                    selecting_mode=state.selecting_mode,
                    submission_overflow=state.submission_overflow,
                    publish_mode="manual",
                    result_mode=state.result_mode,
                    author_reveal=state.author_reveal,
                    author_reveal_zero=state.author_reveal_zero,
                    select_label_specs=state.select_label_specs,
                )
                kukai_id = kukai.id
                kukai_title = kukai.title
        except ServiceError as e:
            await _safe_delete_channel()
            await interaction.edit_original_response(embed=error_embed(str(e)), view=None)
            return
        except Exception:
            logger.exception("Unhandled error during kukai wizard confirmation")
            await _safe_delete_channel()
            await interaction.edit_original_response(
                embed=error_embed(
                    "句会作成中に内部エラーが発生しました。"
                    "\nBotログを確認してください（チャンネルはロールバック済み）。"
                ),
                view=None,
            )
            return

        schedule_warning: str | None = None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, guild.id)
                await notification_service.schedule_kukai_jobs(session, kukai)
        except Exception:
            logger.exception("Failed to schedule jobs for newly created kukai (id=%s)", kukai_id)
            schedule_warning = (
                "通知ジョブの登録に失敗しました。`/kukai pause`→`/kukai resume` で再登録を試してください。"
            )

        clear_wizard(state.user_id)

        success_description = (
            f"句会「**{kukai_title}**」を作成しました。\n"
            f"チャンネル: {channel.mention}\n"
            f"句会ID: `{kukai_id}`\n\n"
            f"`/kukai proceed kukai_id:{kukai_id}` で受付を開始できます。\n"
            "このウィザードは完了しました（再操作不可）。"
        )
        if name_collision_warning:
            success_description += f"\n\n{name_collision_warning}"
        if schedule_warning:
            success_description += f"\n\n⚠️ {schedule_warning}"

        success_embed_ = discord.Embed(
            title="✅ 句会作成完了",
            description=success_description,
            color=COLOR_SUCCESS,
        )
        await interaction.edit_original_response(embed=success_embed_, view=None)

        # Post info embed to the new channel
        entry_deadline = format_jst(state.entry_close_at) if state.entry_close_at else "未定"
        sub_str = format_jst(state.submission_close_at) if state.submission_close_at else "未定"
        selecting_str = format_jst(state.selecting_close_at) if state.selecting_close_at else "未定"
        info = discord.Embed(
            title=f"📋 {kukai_title}",
            description=state.description or "",
            color=COLOR_INFO,
        )
        if state.theme:
            info.add_field(name="題", value=state.theme, inline=True)
        info.add_field(name="エントリー締切", value=entry_deadline, inline=False)
        info.add_field(name="投句締切", value=sub_str, inline=False)
        info.add_field(name="選句締切", value=selecting_str, inline=False)
        info.set_footer(text=f"句会ID: {kukai_id}")
        await channel.send(embed=info)

        if interaction.channel and interaction.channel.id != channel.id:
            try:
                await interaction.channel.send(
                    f"📣 句会「**{kukai_title}**」を作成しました。"
                    f" 開催チャンネル: {channel.mention} / 句会ID: `{kukai_id}`"
                )
            except Exception:
                logger.exception("Failed to announce kukai creation in original channel")
