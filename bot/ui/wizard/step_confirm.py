"""Wizard step 6: Confirmation + kukai creation."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import discord

from bot.database import get_session
from bot.services import kukai_service, notification_service, voice_service
from bot.services.errors import ServiceError
from bot.models.voice_session import VoiceSession
from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, clear_wizard
from bot.utils.datetime_utils import format_jst
from bot.utils.embed_builder import COLOR_INFO, COLOR_SUCCESS, build_select_summary, error_embed

logger = logging.getLogger(__name__)
AUTHOR_PUBLICATION_LABELS = {
    "with_result": "結果公開と同時に作者を公開",
    "manual": "結果公開後に作者を手動公開",
    "never": "作者公開はしない",
}


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(
        title=f"ステップ 9/{STEP_COUNT}: 確認",
        description="以下の設定で句会を作成します。よろしければ「作成」を押してください。",
        color=discord.Color.green(),
    )
    embed.add_field(name="句会名", value=state.title, inline=False)
    if state.theme:
        embed.add_field(name="題", value=state.theme, inline=True)
    if state.description:
        embed.add_field(name="説明", value=state.description[:200], inline=False)

    sub_str = format_jst(state.submission_close_at) if state.submission_close_at else "未設定"
    selecting_str = format_jst(state.selecting_close_at) if state.selecting_close_at else "未設定"
    entry_mode_labels = {"manual": "手動", "auto": "自動", "full_auto": "自動"}
    sub_mode_labels = {"manual": "手動", "semi_auto": "半自動", "full_auto": "全自動"}
    if state.entry_enabled:
        entry_str = format_jst(state.entry_close_at) if state.entry_close_at else "未設定"
        embed.add_field(
            name=f"エントリー締切（{entry_mode_labels.get(state.entry_mode, state.entry_mode)}）",
            value=entry_str,
            inline=True,
        )
    embed.add_field(
        name=f"投句締切（{sub_mode_labels.get(state.submission_mode, state.submission_mode)}）",
        value=sub_str,
        inline=True,
    )
    embed.add_field(
        name=f"選句締切（{sub_mode_labels.get(state.selecting_mode, state.selecting_mode)}）",
        value=selecting_str,
        inline=True,
    )

    entry_str = "有効" if state.entry_enabled else "無効"
    if state.entry_enabled:
        entry_str += (
            f"　承認: {'要' if state.entry_approval else '不要'}"
            f"　締切進行: {entry_mode_labels.get(state.entry_mode, state.entry_mode)}"
        )
    embed.add_field(name="エントリー", value=entry_str, inline=True)

    max_label = "∞（無制限）" if state.submission_max is None else str(state.submission_max)
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
            f"作者公開設定: {AUTHOR_PUBLICATION_LABELS.get(state.author_publication_mode, state.author_publication_mode)}"
            f"　0点以下作者: {('適用外' if state.author_publication_mode == 'never' else ('公開' if state.author_reveal_zero else '非公開'))}"
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
    if state.voice_enabled:
        voice_lines = [
            f"場所: <#{state.voice_channel_id}>" if state.voice_channel_id else "場所: 未選択",
            f"開始: {format_jst(state.voice_start_at)}" if state.voice_start_at else "開始: 未設定",
        ]
        if state.voice_end_at:
            voice_lines.append(f"終了: {format_jst(state.voice_end_at)}")
        embed.add_field(name="ボイス句会", value="\n".join(voice_lines), inline=False)
    notify_value = (
        f"{state.notify_preset_name}（{len(state.notification_specs)}件）"
        if state.notification_specs
        else "デフォルト（エントリー・投句・選句24時間前）"
    )
    embed.add_field(name="通知", value=notify_value, inline=False)
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

        cancel_btn = discord.ui.Button(
            label="❌ キャンセル", style=discord.ButtonStyle.danger, row=0
        )
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

        back_btn = discord.ui.Button(
            label="← 戻る", style=discord.ButtonStyle.secondary, row=0
        )
        back_btn.callback = self._back
        self.add_item(back_btn)

        confirm_btn = discord.ui.Button(
            label="✅ 作成",
            style=discord.ButtonStyle.success,
            disabled=not state.can_confirm,
            row=0,
        )
        confirm_btn.callback = self._confirm
        self.add_item(confirm_btn)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 8
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self.state
        entry_mode_labels = {"manual": "手動", "auto": "自動", "full_auto": "自動"}
        sub_mode_labels = {"manual": "手動", "semi_auto": "半自動", "full_auto": "全自動"}
        guild = interaction.guild
        assert guild is not None

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_deadlines = []
        if state.entry_close_at is not None and state.entry_close_at <= now:
            stale_deadlines.append("エントリー締切")
        if state.submission_close_at is not None and state.submission_close_at <= now:
            stale_deadlines.append("投句締切")
        if state.selecting_close_at is not None and state.selecting_close_at <= now:
            stale_deadlines.append("選句締切")
        if stale_deadlines:
            await interaction.edit_original_response(
                embed=error_embed(
                    "締切時刻が現在時刻を過ぎています。"
                    f"\n対象: {', '.join(stale_deadlines)}"
                    "\nステップ3で日程を設定し直してください。"
                ),
                view=None,
            )
            return

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

        voice_sess: VoiceSession | None = None
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
                    entry_mode=state.entry_mode,
                    min_participants=state.min_participants,
                    submission_min=state.submission_min,
                    submission_max=state.submission_max,
                    submission_mode=state.submission_mode,
                    selecting_mode=state.selecting_mode,
                    submission_overflow=state.submission_overflow,
                    points_enabled=state.select_points_enabled,
                    publish_mode="manual",
                    author_publication_mode=state.author_publication_mode,
                    author_reveal_zero=state.author_reveal_zero,
                    select_label_specs=state.select_label_specs,
                )
                if state.voice_enabled and state.voice_channel_id and state.voice_start_at:
                    voice_sess = await voice_service.upsert_voice_session(
                        session,
                        kukai,
                        vc_channel_id=state.voice_channel_id,
                        start_at=state.voice_start_at,
                        end_at=state.voice_end_at,
                    )
                if state.notification_specs:
                    await notification_service.replace_notification_schedules(
                        session,
                        kukai,
                        state.notification_specs,
                    )
                kukai_id = kukai.id
                kukai_title = kukai.title
                # Resolve preset custom summary text if a template was used
                summary_override: str | None = None
                from bot.services import select_rule_service as _srs
                template_id_used = next(
                    (s.get("template_id") for s in state.select_label_specs if s.get("template_id")),
                    None,
                )
                if template_id_used:
                    try:
                        tmpl = await _srs.get_template(session, guild.id, template_id_used)
                        summary_override = _srs.get_template_info_text(tmpl.definition_json)
                    except Exception:
                        pass
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

        # Create Discord scheduled event (after DB commit succeeds)
        if voice_sess is not None:
            event_id = await voice_service.create_discord_event(guild, kukai, voice_sess)
            if event_id is not None:
                async with get_session() as sess2:
                    from sqlalchemy import select as sa_select
                    res = await sess2.execute(
                        sa_select(VoiceSession).where(VoiceSession.kukai_id == kukai_id)
                    )
                    vs = res.scalar_one_or_none()
                    if vs is not None:
                        vs.discord_event_id = event_id

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
            "投句受付は `/kukai proceed` で開始します。\n"
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
        sub_str = format_jst(state.submission_close_at) if state.submission_close_at else "未定"
        selecting_str = format_jst(state.selecting_close_at) if state.selecting_close_at else "未定"
        info = discord.Embed(
            title=f"📋 {kukai_title}",
            description=state.description or "",
            color=COLOR_INFO,
        )
        if state.theme:
            info.add_field(name="題", value=state.theme, inline=True)
        summary = build_select_summary(
            state.submission_min, state.submission_max, state.select_label_specs,
            override_text=summary_override,
        )
        info.add_field(name="句数", value=summary, inline=False)
        if state.entry_enabled:
            entry_deadline = format_jst(state.entry_close_at) if state.entry_close_at else "未定"
            info.add_field(
                name=f"エントリー締切（{entry_mode_labels.get(state.entry_mode, state.entry_mode)}）",
                value=entry_deadline,
                inline=False,
            )
        info.add_field(
            name=f"投句締切（{sub_mode_labels.get(state.submission_mode, state.submission_mode)}）",
            value=sub_str,
            inline=False,
        )
        info.add_field(
            name=f"選句締切（{sub_mode_labels.get(state.selecting_mode, state.selecting_mode)}）",
            value=selecting_str,
            inline=False,
        )
        if state.voice_enabled and state.voice_channel_id and state.voice_start_at:
            voice_value = f"開始: {format_jst(state.voice_start_at)}\n場所: <#{state.voice_channel_id}>"
            if state.voice_end_at:
                voice_value += f"\n終了: {format_jst(state.voice_end_at)}"
            info.add_field(name="ボイス句会", value=voice_value, inline=False)
        info.set_footer(text=f"句会ID: {kukai_id}")
        await channel.send(embed=info)

        if state.entry_enabled:
            from bot.cogs.kukai_cog import StageActionView
            from bot.state_machine.states import KukaiState

            entry_embed = discord.Embed(
                description=f"句会「**{kukai_title}**」の **エントリー受付** を開始しました。",
                color=COLOR_INFO,
            )
            if state.entry_close_at:
                entry_embed.add_field(
                    name=f"エントリー締切（{entry_mode_labels.get(state.entry_mode, state.entry_mode)}）",
                    value=format_jst(state.entry_close_at),
                    inline=False,
                )
            entry_embed.set_footer(text=f"句会ID: {kukai_id}")
            await channel.send(
                embed=entry_embed,
                view=StageActionView(kukai_id, KukaiState.ENTRY_OPEN),
            )

        if interaction.channel and interaction.channel.id != channel.id:
            try:
                await interaction.channel.send(
                    f"📣 句会「**{kukai_title}**」を作成しました。"
                    f" 開催チャンネル: {channel.mention} / 句会ID: `{kukai_id}`"
                )
            except Exception:
                logger.exception("Failed to announce kukai creation in original channel")
