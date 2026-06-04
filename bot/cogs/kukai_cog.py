"""Kukai management commands: /kukai *"""

import asyncio
import logging
import re

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.repositories import select_repo
from bot.services import (
    admin_notice_service,
    kukai_service,
    notification_service,
    permission_service,
    progress_service,
    result_service,
    select_rule_service,
    submission_service,
    voice_service,
)
from bot.services.errors import ServiceError
from bot.state_machine.states import KukaiState
from bot.ui.common import ConfirmView
from bot.ui.submission_view import RollbackView
from bot.utils.bulk_parser import (
    BulkParseError,
    first_value,
    parse_bool,
    parse_datetime_field,
    parse_fields,
    parse_int,
    parse_label_spec,
    parse_optional_int,
    parse_reminder_spec,
    reject_unknown_keys,
    values_for,
)
from bot.utils.channel import effective_channel_id
from bot.utils.discord_retry import send_with_retry
from bot.utils.datetime_utils import format_jst, parse_datetime
from bot.utils.submission_publish import build_submission_publish_embeds
from bot.utils.embed_builder import (
    COLOR_INFO,
    COLOR_RESULT,
    COLOR_SUCCESS,
    build_select_summary,
    error_embed,
    success_embed,
)

logger = logging.getLogger(__name__)

# Japanese labels for each state
STATE_LABEL: dict[str, str] = {
    "draft": "開始前",
    "entry_open": "エントリー受付中",
    "entry_closed": "エントリー締切",
    "submission_open": "投句受付中",
    "submission_closed": "投句締切",
    "waiting_publish": "投句公開待ち",
    "selecting_open": "選句受付中",
    "selecting_closed": "選句締切",
    "waiting_results": "結果公開待ち",
    "results": "結果公開中",
    "ended": "終了",
    "paused": "一時停止",
    "cancelled": "中止",
}


def _mode_label(mode: str | None) -> str:
    return {
        "manual": "手動",
        "semi_auto": "半自動",
        "full_auto": "全自動",
        "auto": "自動",
    }.get(str(mode), str(mode))


def _entry_mode_label(mode: str | None) -> str:
    return {"manual": "手動", "auto": "自動", "full_auto": "自動"}.get(str(mode), str(mode))

ROLLBACK_TARGET_CHOICES = [
    app_commands.Choice(name="開始前", value=KukaiState.DRAFT.value),
    app_commands.Choice(name="エントリー受付中", value=KukaiState.ENTRY_OPEN.value),
    app_commands.Choice(name="エントリー締切", value=KukaiState.ENTRY_CLOSED.value),
    app_commands.Choice(name="投句受付中", value=KukaiState.SUBMISSION_OPEN.value),
    app_commands.Choice(name="投句締切", value=KukaiState.SUBMISSION_CLOSED.value),
    app_commands.Choice(name="投句公開待ち", value=KukaiState.WAITING_PUBLISH.value),
    app_commands.Choice(name="選句受付中", value=KukaiState.SELECTING_OPEN.value),
    app_commands.Choice(name="選句締切", value=KukaiState.SELECTING_CLOSED.value),
]


def stage_action_custom_id(kukai_id: int, state: KukaiState) -> str:
    return f"kukai:stage:{kukai_id}:{state.value}"


class StageActionView(discord.ui.View):
    def __init__(self, kukai_id: int, state: KukaiState) -> None:
        super().__init__(timeout=None)
        self.kukai_id = kukai_id
        self.state = state
        label_map = {
            KukaiState.ENTRY_OPEN: "エントリーする",
            KukaiState.SUBMISSION_OPEN: "投句する",
            KukaiState.SELECTING_OPEN: "選句する",
        }
        button_label = label_map.get(state)
        if not button_label:
            return
        button = discord.ui.Button(
            label=button_label,
            style=discord.ButtonStyle.primary,
            row=0,
            custom_id=stage_action_custom_id(kukai_id, state),
        )

        async def _callback(interaction: discord.Interaction) -> None:
            assert interaction.guild is not None
            try:
                if self.state == KukaiState.ENTRY_OPEN:
                    from bot.cogs.entry_cog import EntryHaigoModal

                    await interaction.response.send_modal(
                        EntryHaigoModal(
                            kukai_id=self.kukai_id,
                            channel_id=effective_channel_id(interaction),
                            guild_id=interaction.guild.id,
                        )
                    )
                    return

                async with get_session() as session:
                    kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                    current = KukaiState.from_value(kukai.state)

                    if self.state == KukaiState.SUBMISSION_OPEN:
                        if current != KukaiState.SUBMISSION_OPEN:
                            await interaction.response.send_message(
                                embed=error_embed("現在は投句受付中ではありません。"),
                                ephemeral=True,
                            )
                            return
                        subs = await submission_service.list_user_submissions(
                            session, kukai.id, interaction.user.id
                        )
                        from bot.ui.submission_view import SubmissionView, _submissions_embed

                        await interaction.response.send_message(
                            embed=_submissions_embed(kukai, subs),
                            view=SubmissionView(kukai.id, subs, kukai),
                            ephemeral=True,
                        )
                        return

                    if self.state == KukaiState.SELECTING_OPEN:
                        if current != KukaiState.SELECTING_OPEN:
                            await interaction.response.send_message(
                                embed=error_embed("現在は選句受付中ではありません。"),
                                ephemeral=True,
                            )
                            return
                        from bot.models.select_rule import SelectLabel
                        from bot.ui.select_view import SelectView, load_select_data

                        pub_subs, labels, selects_by_sub, overall_comment = await load_select_data(
                            session, kukai.id, interaction.user.id
                        )
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
                            pub_subs, labels, selects_by_sub, overall_comment = await load_select_data(
                                session, kukai.id, interaction.user.id
                            )
                        if not pub_subs:
                            await interaction.response.send_message(
                                embed=discord.Embed(description="公開済みの投句がありません。", color=COLOR_INFO),
                                ephemeral=True,
                            )
                            return
                        view = SelectView(
                            kukai,
                            pub_subs,
                            labels,
                            selects_by_sub,
                            overall_comment=overall_comment,
                            selector_user_id=interaction.user.id,
                        )
                        await interaction.response.send_message(
                            embed=view.build_embed(),
                            view=view,
                            ephemeral=True,
                        )
                        return

            except ServiceError as e:
                await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

        button.callback = _callback
        self.add_item(button)


class SelectRuleConfigModal(discord.ui.Modal, title="選句ルール設定"):
    def __init__(self, cog: "KukaiCog", kukai_id: int | None) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.kukai_id = kukai_id
        self.config = discord.ui.TextInput(
            label="選句ルール設定",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1900,
            placeholder=(
                "preset_id=3\n"
                "または\n"
                "points_enabled=true\n"
                "label=特選,2,1,1,optional\n"
                "label=並選,1,0,5,optional"
            ),
        )
        self.add_item(self.config)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog._edit_select_rule_config(
            interaction,
            kukai_id=self.kukai_id,
            select_rule_config=str(self.config.value),
        )



class KukaiCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    kukai = app_commands.Group(name="kukai", description="句会の管理")
    kukai_admin_grp = app_commands.Group(name="admin", description="句会管理者・データ操作", parent=kukai)
    kukai_notify_grp = app_commands.Group(name="notify", description="通知設定の管理", parent=kukai)

    # ------------------------------------------------------------------
    # Top-level participant commands
    # ------------------------------------------------------------------

    @app_commands.command(name="list", description="このサーバーの開催中・招集中の句会一覧を表示します")
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

    @app_commands.command(name="info", description="句会の詳細を表示します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def kukai_info(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        try:
            from sqlalchemy import select as _sa_select
            from sqlalchemy.orm import selectinload
            from bot.models.kukai import Kukai as _Kukai
            from bot.models.voice_session import VoiceSession as _VoiceSession
            async with get_session() as session:
                resolved = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                result = await session.execute(
                    _sa_select(_Kukai)
                    .where(_Kukai.id == resolved.id, _Kukai.guild_id == interaction.guild.id)
                    .options(
                        selectinload(_Kukai.select_labels),
                        selectinload(_Kukai.voice_session),
                    )
                )
                kukai = result.scalar_one_or_none()
                if kukai is None:
                    await interaction.response.send_message(
                        embed=error_embed(f"句会 ID {resolved.id} が見つかりません。"), ephemeral=True
                    )
                    return
                select_labels = list(kukai.select_labels)
                voice_session = (
                    await session.execute(
                        _sa_select(_VoiceSession).where(_VoiceSession.kukai_id == kukai.id)
                    )
                ).scalar_one_or_none()
            embed = _build_info_embed(
                kukai,
                select_labels=select_labels,
                voice_session=voice_session,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------

    @kukai.command(name="create", description="新しい句会を作成します（ウィザード形式）")
    async def kukai_create(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            allowed = await permission_service.can_create_kukai(
                session, interaction.guild.id, interaction.user  # type: ignore[arg-type]
            )
            templates = await select_rule_service.list_templates(session, interaction.guild.id)
        if not allowed:
            await interaction.followup.send(
                embed=error_embed("句会の作成権限がありません。"), ephemeral=True
            )
            return

        from bot.ui.wizard.base import goto_step
        from bot.ui.wizard.wizard_state import WizardState, set_wizard

        state = WizardState(user_id=interaction.user.id, guild_id=interaction.guild.id)
        state.select_preset_options = [{"id": t.id, "name": t.name} for t in templates]
        default_template = next((t for t in templates if t.is_default), None)
        if default_template is not None:
            points_enabled, _ = select_rule_service.deserialize_template_payload(
                default_template.definition_json
            )
            state.select_preset_template_id = default_template.id
            state.select_preset_name = default_template.name
            state.select_points_enabled = points_enabled
            state.select_label_specs = select_rule_service.build_kukai_specs_from_template(
                default_template
            )
        else:
            state.select_label_specs = select_rule_service.default_kukai_specs()
        state.selected_select_label = next(
            (
                str(spec["label"])
                for spec in state.select_label_specs
                if spec["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
            ),
            "特選",
        )
        set_wizard(state)
        await goto_step(interaction, state, first_send=True)

    @kukai.command(name="create-bulk", description="【作成権限者】行形式で新しい句会を一括作成します")
    @app_commands.describe(config="title=... / submission_close_at=... / selecting_close_at=... / label=...")
    async def kukai_create_bulk(self, interaction: discord.Interaction, config: str) -> None:
        assert interaction.guild is not None
        try:
            fields = parse_fields(config)
            reject_unknown_keys(
                fields,
                {
                    "title",
                    "theme",
                    "description",
                    "channel",
                    "channel_name",
                    "category_id",
                    "entry_enabled",
                    "entry_approval",
                    "entry_mode",
                    "min_participants",
                    "entry_close_at",
                    "submission_open_at",
                    "submission_close_at",
                    "selecting_close_at",
                    "submission_min",
                    "submission_max",
                    "submission_overflow",
                    "submission_mode",
                    "selecting_mode",
                    "publish_mode",
                    "result_mode",
                    "author_publication_mode",
                    "author_reveal",
                    "author_reveal_zero",
                    "preset_id",
                    "label",
                    "voice_enabled",
                    "voice_channel",
                    "voice_start_at",
                    "voice_end_at",
                    "reminder",
                },
            )
            title = first_value(fields, "title")
            if not title:
                raise BulkParseError("title は必須です。")
            submission_close_raw = first_value(fields, "submission_close_at")
            selecting_close_raw = first_value(fields, "selecting_close_at")
            if not submission_close_raw or not selecting_close_raw:
                raise BulkParseError("submission_close_at と selecting_close_at は必須です。")

            entry_enabled = parse_bool(
                first_value(fields, "entry_enabled", "true") or "true",
                name="entry_enabled",
            )
            entry_close_at = None
            entry_close_raw = first_value(fields, "entry_close_at")
            if entry_enabled:
                if entry_close_raw:
                    entry_close_at = parse_datetime_field(entry_close_raw, name="entry_close_at")

            entry_mode = first_value(fields, "entry_mode", "manual") or "manual"
            if entry_mode == "full_auto":
                entry_mode = "auto"
            if entry_mode not in {"manual", "auto"}:
                raise BulkParseError("entry_mode は manual/auto で指定してください。")

            submission_open_raw = first_value(fields, "submission_open_at")
            submission_open_at = (
                parse_datetime_field(submission_open_raw, name="submission_open_at")
                if submission_open_raw
                else None
            )
            submission_close_at = parse_datetime_field(submission_close_raw, name="submission_close_at")
            selecting_close_at = parse_datetime_field(selecting_close_raw, name="selecting_close_at")
            submission_min = parse_int(
                first_value(fields, "submission_min", "1") or "1",
                name="submission_min",
                min_value=1,
            )
            submission_max_raw = first_value(fields, "submission_max", "5")
            submission_max = parse_optional_int(
                submission_max_raw if submission_max_raw is not None else "5",
                name="submission_max",
                min_value=1,
            )
            if submission_max is not None and submission_max < submission_min:
                raise BulkParseError("submission_max は submission_min 以上にしてください。")
            min_participants = parse_int(
                first_value(fields, "min_participants", "0") or "0",
                name="min_participants",
                min_value=0,
            )
            category_id_raw = first_value(fields, "category_id")
            category_id = (
                parse_int(category_id_raw, name="category_id", min_value=1)
                if category_id_raw
                else None
            )
            preset_id_raw = first_value(fields, "preset_id")
            preset_id = parse_int(preset_id_raw, name="preset_id", min_value=1) if preset_id_raw else None

            label_specs = [
                parse_label_spec(field.value, line_no=field.line_no)
                for field in values_for(fields, "label")
            ]
            voice_enabled = parse_bool(
                first_value(fields, "voice_enabled", "false") or "false",
                name="voice_enabled",
            )
            voice_channel_raw = first_value(fields, "voice_channel")
            voice_start_raw = first_value(fields, "voice_start_at")
            voice_end_raw = first_value(fields, "voice_end_at")
            if voice_enabled and (not voice_channel_raw or not voice_start_raw):
                raise BulkParseError("voice_enabled=true の場合 voice_channel と voice_start_at は必須です。")
            voice_channel_id: int | None = None
            voice_start_at = None
            voice_end_at = None
            if voice_enabled:
                voice_match = re.fullmatch(r"<?#?(\d+)>?", voice_channel_raw or "")
                if not voice_match:
                    raise BulkParseError("voice_channel は <#ボイスチャンネルID> または ID で指定してください。")
                voice_channel_id = int(voice_match.group(1))
                voice_start_at = parse_datetime_field(voice_start_raw or "", name="voice_start_at")
                if voice_end_raw:
                    voice_end_at = parse_datetime_field(voice_end_raw, name="voice_end_at")
                if voice_end_at is not None and voice_end_at <= voice_start_at:
                    raise BulkParseError("voice_end_at は voice_start_at より後にしてください。")
            reminder_specs = [
                parse_reminder_spec(field.value, line_no=field.line_no)
                for field in values_for(fields, "reminder")
            ]
            submission_mode = first_value(fields, "submission_mode", "manual") or "manual"
            selecting_mode = first_value(fields, "selecting_mode", "manual") or "manual"
            publish_mode = first_value(fields, "publish_mode", "manual") or "manual"
            result_mode = first_value(fields, "result_mode", "manual") or "manual"
            author_publication_mode = first_value(fields, "author_publication_mode")
            legacy_author_reveal_raw = first_value(fields, "author_reveal")
            if not author_publication_mode:
                if legacy_author_reveal_raw is None:
                    author_publication_mode = "with_result"
                else:
                    author_publication_mode = (
                        "with_result"
                        if parse_bool(legacy_author_reveal_raw, name="author_reveal")
                        else "never"
                    )
            for name, value, allowed in (
                ("submission_mode", submission_mode, {"manual", "semi_auto", "full_auto"}),
                ("selecting_mode", selecting_mode, {"manual", "semi_auto", "full_auto"}),
                ("publish_mode", publish_mode, {"manual", "auto"}),
                ("result_mode", result_mode, {"manual", "auto"}),
                ("author_publication_mode", author_publication_mode, {"with_result", "manual", "never"}),
            ):
                if value not in allowed:
                    raise BulkParseError(f"{name} は {('/'.join(sorted(allowed)))} で指定してください。")
        except BulkParseError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        async with get_session() as session:
            allowed = await permission_service.can_create_kukai(
                session, interaction.guild.id, interaction.user  # type: ignore[arg-type]
            )
        if not allowed:
            await interaction.response.send_message(
                embed=error_embed("句会の作成権限がありません。"), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        channel_setting = (first_value(fields, "channel", "current") or "current").strip().lower()
        channel: discord.TextChannel | None = None
        created_new_channel = False
        name_collision_warning: str | None = None
        try:
            if channel_setting == "current":
                if not isinstance(interaction.channel, discord.TextChannel):
                    raise BulkParseError("channel=current はテキストチャンネルで実行してください。")
                channel = interaction.channel
            elif channel_setting == "new":
                ch_name = _sanitize_channel_name(first_value(fields, "channel_name") or title)
                existing_same_name = [ch for ch in interaction.guild.text_channels if ch.name == ch_name]
                if existing_same_name:
                    name_collision_warning = f"同名チャンネル「{ch_name}」が既に存在します。"
                category = None
                if category_id:
                    candidate = interaction.guild.get_channel(category_id)
                    if isinstance(candidate, discord.CategoryChannel):
                        category = candidate
                channel = await interaction.guild.create_text_channel(ch_name, category=category)
                created_new_channel = True
            else:
                channel_id_match = re.fullmatch(r"<?#?(\d+)>?", channel_setting)
                if not channel_id_match:
                    raise BulkParseError("channel は current/new/<#チャンネルID> のいずれかで指定してください。")
                candidate = interaction.guild.get_channel(int(channel_id_match.group(1)))
                if not isinstance(candidate, discord.TextChannel):
                    raise BulkParseError("指定された channel が見つからないか、テキストチャンネルではありません。")
                channel = candidate
        except discord.Forbidden:
            await interaction.edit_original_response(
                embed=error_embed("チャンネルを作成する権限がありません。Botにチャンネル管理権限を付与してください。")
            )
            return
        except BulkParseError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)))
            return

        if voice_enabled and voice_channel_id is not None:
            voice_channel = interaction.guild.get_channel(voice_channel_id)
            if not isinstance(voice_channel, (discord.VoiceChannel, discord.StageChannel)):
                await interaction.edit_original_response(
                    embed=error_embed("voice_channel が見つからないか、ボイス/ステージチャンネルではありません。")
                )
                return

        async def _safe_delete_channel() -> None:
            if created_new_channel and channel is not None:
                try:
                    await channel.delete()
                except Exception:
                    pass

        try:
            select_label_specs = label_specs
            points_enabled = True
            bulk_summary_override: str | None = None
            async with get_session() as session:
                if not select_label_specs and preset_id is not None:
                    template = await select_rule_service.get_template(
                        session, interaction.guild.id, preset_id
                    )
                    points_enabled, _ = select_rule_service.deserialize_template_payload(
                        template.definition_json
                    )
                    bulk_summary_override = select_rule_service.get_template_info_text(
                        template.definition_json
                    )
                    select_label_specs = select_rule_service.build_kukai_specs_from_template(template)
                elif not select_label_specs:
                    select_label_specs = select_rule_service.default_kukai_specs()

                kukai = await kukai_service.create_kukai(
                    session,
                    guild_id=interaction.guild.id,
                    created_by=interaction.user.id,
                    channel_id=channel.id,
                    title=title,
                    theme=first_value(fields, "theme") or None,
                    description=first_value(fields, "description") or None,
                    entry_close_at=entry_close_at,
                    submission_open_at=submission_open_at,
                    submission_close_at=submission_close_at,
                    selecting_close_at=selecting_close_at,
                    entry_enabled=entry_enabled,
                    entry_approval=parse_bool(
                        first_value(fields, "entry_approval", "false") or "false",
                        name="entry_approval",
                    ),
                    entry_mode=entry_mode,
                    min_participants=min_participants,
                    submission_min=submission_min,
                    submission_max=submission_max,
                    submission_mode=submission_mode,
                    selecting_mode=selecting_mode,
                    submission_overflow=parse_bool(
                        first_value(fields, "submission_overflow", "false") or "false",
                        name="submission_overflow",
                    ),
                    points_enabled=points_enabled,
                    publish_mode=publish_mode,
                    result_mode=result_mode,
                    author_publication_mode=author_publication_mode,
                    author_reveal_zero=parse_bool(
                        first_value(fields, "author_reveal_zero", "true") or "true",
                        name="author_reveal_zero",
                    ),
                    select_label_specs=select_label_specs,
                )
                bulk_voice_sess = None
                if voice_enabled and voice_channel_id is not None:
                    bulk_voice_sess = await voice_service.upsert_voice_session(
                        session,
                        kukai,
                        vc_channel_id=voice_channel_id,
                        start_at=voice_start_at,
                        end_at=voice_end_at,
                    )
                if reminder_specs:
                    await notification_service.replace_notification_schedules(
                        session, kukai, reminder_specs
                    )
                kukai_id = kukai.id
                kukai_title = kukai.title
                await notification_service.schedule_kukai_jobs(session, kukai)
        except (BulkParseError, ServiceError) as e:
            await _safe_delete_channel()
            await interaction.edit_original_response(embed=error_embed(str(e)))
            return
        except Exception:
            await _safe_delete_channel()
            await interaction.edit_original_response(embed=error_embed("句会作成中に内部エラーが発生しました。"))
            raise

        # Create Discord scheduled event after successful DB commit
        if bulk_voice_sess is not None:
            from bot.models.voice_session import VoiceSession as _VoiceSession
            event_id = await voice_service.create_discord_event(interaction.guild, kukai, bulk_voice_sess)
            if event_id is not None:
                async with get_session() as sess2:
                    from sqlalchemy import select as _sa_select
                    res = await sess2.execute(
                        _sa_select(_VoiceSession).where(_VoiceSession.kukai_id == kukai_id)
                    )
                    vs = res.scalar_one_or_none()
                    if vs is not None:
                        vs.discord_event_id = event_id

        info = discord.Embed(
            title=f"📋 {kukai_title}",
            description=first_value(fields, "description") or "",
            color=COLOR_INFO,
        )
        if first_value(fields, "theme"):
            info.add_field(name="題", value=first_value(fields, "theme"), inline=True)
        info.add_field(
            name="句数",
            value=build_select_summary(
                submission_min, submission_max, select_label_specs,
                override_text=bulk_summary_override,
            ),
            inline=False,
        )
        if entry_enabled and entry_close_at:
            info.add_field(
                name=f"エントリー締切（{_entry_mode_label(entry_mode)}）",
                value=format_jst(entry_close_at),
                inline=False,
            )
        if submission_open_at:
            info.add_field(
                name="投句開始",
                value=format_jst(submission_open_at),
                inline=False,
            )
        info.add_field(
            name=f"投句締切（{_mode_label(submission_mode)}）",
            value=format_jst(submission_close_at),
            inline=False,
        )
        info.add_field(
            name=f"選句締切（{_mode_label(selecting_mode)}）",
            value=format_jst(selecting_close_at),
            inline=False,
        )
        if voice_enabled and voice_channel_id is not None and voice_start_at is not None:
            voice_value = f"開始: {format_jst(voice_start_at)}\n場所: <#{voice_channel_id}>"
            if voice_end_at is not None:
                voice_value += f"\n終了: {format_jst(voice_end_at)}"
            info.add_field(name="ボイス句会", value=voice_value, inline=False)
        info.set_footer(text=f"句会ID: {kukai_id}")
        try:
            await channel.send(embed=info)
            if entry_enabled:
                entry_embed = discord.Embed(
                    description=f"句会「**{kukai_title}**」の **エントリー受付** を開始しました。",
                    color=COLOR_INFO,
                )
                if entry_close_at:
                    entry_embed.add_field(
                        name=f"エントリー締切（{_entry_mode_label(entry_mode)}）",
                        value=format_jst(entry_close_at),
                        inline=False,
                    )
                entry_embed.set_footer(text=f"句会ID: {kukai_id}")
                await channel.send(
                    embed=entry_embed,
                    view=StageActionView(kukai_id, KukaiState.ENTRY_OPEN),
                )
        except discord.Forbidden:
            name_collision_warning = (name_collision_warning or "") + "\n開催チャンネルへの投稿権限がありません。"

        message = (
            f"句会「**{kukai_title}**」を作成しました。\n"
            f"チャンネル: {channel.mention}\n"
            f"句会ID: `{kukai_id}`"
        )
        if name_collision_warning:
            message += f"\n\n⚠️ {name_collision_warning.strip()}"
        await interaction.edit_original_response(embed=success_embed(message))

    @kukai.command(name="proceed", description="【句会管理者】句会を次の状態へ進めます")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def kukai_proceed(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            published_count: int | None = None
            publish_warning: str | None = None
            result_count: int | None = None
            result_warning: str | None = None
            interaction_channel_id = effective_channel_id(interaction)
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=interaction_channel_id,
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.followup.send(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                current_state = KukaiState.from_value(kukai.state)
                logger.info(
                    "event=kukai_proceed_command_start kukai_id=%s actor_user_id=%s "
                    "before_state=%s interaction_id=%s channel_id=%s",
                    kukai.id,
                    interaction.user.id,
                    current_state,
                    getattr(interaction, "id", None),
                    interaction_channel_id,
                )
                override_report = await progress_service.report_for_state(session, kukai, current_state)
                if override_report is not None and not override_report.complete:
                    view = ConfirmView(timeout=60)
                    if view.children:
                        view.children[0].label = "それでも進める"  # type: ignore[attr-defined]
                    warning = discord.Embed(
                        title="条件未達の参加者がいます",
                        description=(
                            f"{override_report.summary()}\n"
                            "このまま進行すると、未達の参加者がいる状態で次の段階へ進みます。"
                        ),
                        color=COLOR_INFO,
                    )
                    warning.add_field(
                        name="未達状況",
                        value=_limited_field_value(override_report.admin_lines()),
                        inline=False,
                    )
                    warning.set_footer(text=f"句会ID: {kukai.id}")
                    await interaction.followup.send(embed=warning, view=view, ephemeral=True)
                    await view.wait()
                    if not view.confirmed:
                        await interaction.followup.send(
                            embed=success_embed("進行をキャンセルしました。"),
                            ephemeral=True,
                        )
                        return
                else:
                    override_report = None
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
                if new_state == KukaiState.SUBMISSION_CLOSED:
                    from bot.scheduler import jobs as scheduler_jobs

                    await scheduler_jobs.notify_entry_closed_for_manual_submission_close(
                        bot=self.bot,
                        session=session,
                        kukai=kukai,
                        previous_state=current_state,
                    )
                if override_report is not None:
                    await admin_notice_service.send_admin_notice(
                        self.bot,
                        session,
                        kukai,
                        title="条件未達のまま手動進行しました",
                        description=(
                            f"<@{interaction.user.id}> が `/kukai proceed` で確認し、"
                            "条件未達の参加者がいる状態で句会を進行しました。"
                        ),
                        fields=[("未達状況", "\n".join(override_report.admin_lines()))],
                    )
                await notification_service.cancel_kukai_jobs(session, kukai.id)
                await notification_service.schedule_kukai_jobs(session, kukai)
                logger.info(
                    "event=kukai_proceed_command kukai_id=%s actor_user_id=%s "
                    "before_state=%s after_state=%s interaction_id=%s channel_id=%s",
                    kukai.id,
                    interaction.user.id,
                    current_state,
                    new_state,
                    getattr(interaction, "id", None),
                    interaction_channel_id,
                )
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
            await interaction.followup.send(
                embed=success_embed(description),
                ephemeral=True,
            )
            await self._announce_to_kukai_channel(interaction.guild, kukai, new_state)
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="reveal-authors", description="【句会管理者】結果公開後に作者を公開します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def kukai_reveal_authors(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.edit_original_response(
                        embed=error_embed("この操作は句会管理者のみ実行できます。")
                    )
                    return

                state = KukaiState.from_value(kukai.state)
                if state not in {KukaiState.RESULTS, KukaiState.ENDED}:
                    await interaction.edit_original_response(
                        embed=error_embed("作者公開は結果公開後に実行できます。")
                    )
                    return

                mode = getattr(kukai, "author_publication_mode", "with_result")
                if mode == "never":
                    await interaction.edit_original_response(
                        embed=error_embed("この句会は「作者公開はしない」に設定されています。")
                    )
                    return

                if kukai.author_reveal:
                    await interaction.edit_original_response(
                        embed=success_embed("作者はすでに公開されています。")
                    )
                    return

                kukai.author_reveal = True
                kukai_title = kukai.title
                await self._announce_authors_revealed(interaction.guild, kukai)

            await interaction.edit_original_response(
                embed=success_embed(f"句会「{kukai_title}」の作者を公開しました。")
            )
        except ServiceError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)))

    @kukai.command(name="pause", description="【句会管理者】句会を一時停止します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def kukai_pause(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
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
            await self._announce_to_kukai_channel(interaction.guild, kukai, KukaiState.PAUSED)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="resume", description="【句会管理者】句会を再開します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def kukai_resume(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
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
            await self._announce_to_kukai_channel(interaction.guild, kukai, restored)
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai.command(name="cancel", description="【句会管理者】句会を中止します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def kukai_cancel(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return

            resolved_id = kukai.id
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
                kukai = await kukai_service.get_kukai(session, resolved_id, interaction.guild.id)
                await kukai_service.cancel(session, kukai)
                await notification_service.cancel_kukai_jobs(session, kukai.id)

            await interaction.edit_original_response(
                embed=success_embed(f"句会「{kukai.title}」を中止しました。"),
                view=None,
            )
            await self._announce_to_kukai_channel(interaction.guild, kukai, KukaiState.CANCELLED)
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

    @staticmethod
    def _state_announcement_description(kukai, state: KukaiState) -> str | None:
        stage = KukaiCog._state_stage_label(state)
        if stage:
            return f"句会「**{kukai.title}**」の **{stage}** を開始しました。"
        mapping = {
            KukaiState.ENTRY_CLOSED: "エントリーが締め切られました。",
            KukaiState.SUBMISSION_CLOSED: "投句が締め切られました。",
            KukaiState.WAITING_PUBLISH: "投句公開待ちになりました。",
            KukaiState.SELECTING_CLOSED: "選句が締め切られました。",
            KukaiState.ENDED: "句会が終了しました。",
            KukaiState.PAUSED: "句会が一時停止されました。",
            KukaiState.CANCELLED: "句会が中止されました。",
        }
        message = mapping.get(state)
        if message is None:
            return None
        return f"句会「**{kukai.title}**」: {message}"

    async def _announce_to_kukai_channel(self, guild: discord.Guild, kukai, state: KukaiState) -> None:
        description = self._state_announcement_description(kukai, state)
        if not description:
            return
        if not kukai.channel_id:
            return
        channel = guild.get_channel(kukai.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(description=description, color=COLOR_INFO)
        if state == KukaiState.ENTRY_OPEN and kukai.entry_enabled and kukai.entry_close_at:
            embed.add_field(
                name=f"エントリー締切（{_entry_mode_label(getattr(kukai, 'entry_mode', 'manual'))}）",
                value=format_jst(kukai.entry_close_at),
                inline=False,
            )
        elif state == KukaiState.ENTRY_CLOSED:
            participant_lines = await _approved_entry_lines(interaction_guild=guild, kukai_id=kukai.id)
            embed.add_field(
                name=f"エントリー人数: {len(participant_lines)}名",
                value=_limited_field_value(participant_lines),
                inline=False,
            )
        elif state == KukaiState.SUBMISSION_OPEN and kukai.submission_close_at:
            embed.add_field(
                name=f"投句締切（{_mode_label(kukai.submission_mode)}）",
                value=format_jst(kukai.submission_close_at),
                inline=False,
            )
        elif state == KukaiState.SELECTING_OPEN:
            select_labels = []
            try:
                from bot.models.select_rule import SelectLabel
                from sqlalchemy import select as _sa_select

                async with get_session() as session:
                    result = await session.execute(
                        _sa_select(SelectLabel)
                        .where(SelectLabel.kukai_id == kukai.id)
                        .order_by(SelectLabel.display_order)
                    )
                    select_labels = list(result.scalars().all())
            except Exception:
                logger.exception("Failed to load select labels for stage announcement")
            if select_labels:
                embed.add_field(
                    name="句数",
                    value=build_select_summary(kukai.submission_min, kukai.submission_max, select_labels),
                    inline=False,
                )
            if kukai.selecting_close_at:
                embed.add_field(
                    name=f"選句締切（{_mode_label(kukai.selecting_mode)}）",
                    value=format_jst(kukai.selecting_close_at),
                    inline=False,
                )
        embed.set_footer(text=f"句会ID: {kukai.id}")
        view = StageActionView(kukai.id, state)
        try:
            if view.children:
                await send_with_retry(lambda: channel.send(embed=embed, view=view))
            else:
                await send_with_retry(lambda: channel.send(embed=embed))
        except Exception:
            pass

    async def _announce_authors_revealed(self, guild: discord.Guild, kukai) -> None:
        if not kukai.channel_id:
            return
        channel = guild.get_channel(kukai.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            description=f"句会「**{kukai.title}**」の作者を公開しました。",
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"句会ID: {kukai.id}")
        try:
            await send_with_retry(lambda: channel.send(embed=embed))
        except Exception:
            logger.exception("Failed to announce author reveal")

    async def _announce_settings_updated(
        self,
        guild: discord.Guild,
        kukai,
        *,
        deadlines_changed: bool,
        changed_lines: list[str],
    ) -> None:
        if not kukai.channel_id:
            return
        channel = guild.get_channel(kukai.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title="⚙️ 句会設定を更新しました",
            description=f"句会「**{kukai.title}**」",
            color=COLOR_INFO,
        )
        if changed_lines:
            embed.add_field(name="更新内容", value="\n".join(changed_lines[:20]), inline=False)
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
        from bot.cogs.result_cog import ResultOpenView, build_result_entry_embed, _resolve_initial_format

        first_message_id: int | None = None
        try:
            initial_format = _resolve_initial_format(kukai, None)
            sent = await send_with_retry(
                lambda: channel.send(
                    embed=build_result_entry_embed(kukai, result_count=len(results)),
                    view=ResultOpenView(kukai.id, initial_format=initial_format),
                )
            )
            first_message_id = sent.id
        except discord.Forbidden:
            return len(results), "公開チャンネルへの送信権限がないため、結果を投稿できません。", None

        return len(results), None, first_message_id

    @kukai.command(name="rollback", description="【句会管理者】句会を指定した前段階へ戻します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）", target_state="戻す先の状態")
    @app_commands.choices(target_state=ROLLBACK_TARGET_CHOICES)
    async def kukai_rollback(
        self,
        interaction: discord.Interaction,
        target_state: str,
        kukai_id: int | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.followup.send(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                target = KukaiState.from_value(target_state)
                current = KukaiState.from_value(kukai.state)
                try:
                    submission_service.validate_rollback_target(current, target)
                except ServiceError as e:
                    await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
                    return
                allow_reset_submissions = submission_service.can_reset_submissions_on_rollback(target)

            resolved_id = kukai.id
            current_label = STATE_LABEL.get(current.value, current.value)
            target_label = STATE_LABEL.get(target.value, target.value)
            reset_note = (
                "投句内容をリセットする選択もできます。"
                if allow_reset_submissions
                else "この戻し先では投句内容は保持されます。"
            )
            view = RollbackView(allow_reset_submissions=allow_reset_submissions)
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="⚠️ 句会のロールバック",
                    description=(
                        f"句会「**{kukai.title}**」を **{current_label}** から "
                        f"**{target_label}** へ戻します。\n\n"
                        "戻し先が投句公開待ち以前の場合、投句番号割当は削除されます。\n"
                        f"{reset_note}\n"
                        "選句内容を保持するかリセットするか選んでください。"
                    ),
                    color=discord.Color.orange(),
                ),
                view=view,
            )
            await view.wait()

            if view.choice is None:
                await interaction.edit_original_response(
                    embed=discord.Embed(description="キャンセルしました。", color=COLOR_INFO),
                    view=None,
                )
                return

            keep_submissions = view.choice != "reset_all"
            keep_selects = view.choice == "keep_all"
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, resolved_id, interaction.guild.id)
                await notification_service.cancel_kukai_jobs(session, kukai.id)
                await submission_service.rollback_to_state(
                    session,
                    kukai,
                    target,
                    keep_submissions=keep_submissions,
                    keep_selects=keep_selects,
                )
                await notification_service.schedule_kukai_jobs(session, kukai)

            data_note = (
                "投句・選句を保持"
                if keep_submissions and keep_selects
                else "投句は保持、選句はリセット"
                if keep_submissions
                else "投句・選句をリセット"
            )
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"ロールバックしました。\n状態: **{target_label}**\nデータ: {data_note}",
                    color=COLOR_SUCCESS,
                ),
                view=None,
            )
        except ServiceError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)), view=None)

    @kukai.command(name="edit", description="【句会管理者】句会の設定を変更します")
    @app_commands.describe(
        kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）",
        title="新しいタイトル",
        theme="新しい題（空文字でクリア）",
        description="新しい説明（空文字でクリア）",
        select_rule_config="選句ルール差し替え設定（gui で入力画面 / preset_id=... または label=...）",
        entry_close_at="エントリー締切 (例: 2026-05-20 19:00 JST)",
        submission_open_at="投句開始 (例: 2026-05-20 20:00 JST)",
        submission_close_at="投句締切 (例: 2026-05-20 23:59 JST)",
        selecting_close_at="選句締切 (例: 2026-05-21 23:59 JST)",
        entry_approval="エントリーを承認制にするか",
        min_participants="最小成立人数（0で制限なし）",
        submission_min="最小投句数",
        submission_max="最大投句数",
        submission_max_unlimited="最大投句数を無制限にする",
        submission_overflow="最大投句数を超えた投句を許可するか",
        entry_mode="エントリー締切進行モード",
        submission_mode="投句進行モード",
        selecting_mode="選句進行モード",
        publish_mode="投句公開モード",
        result_mode="結果公開モード",
        author_publication_mode="作者公開設定",
        author_reveal="作者を公開済みにするか",
        author_reveal_zero="0点以下作者を公開するか",
    )
    async def kukai_edit(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
        title: str | None = None,
        theme: str | None = None,
        description: str | None = None,
        select_rule_config: str | None = None,
        entry_close_at: str | None = None,
        submission_open_at: str | None = None,
        submission_close_at: str | None = None,
        selecting_close_at: str | None = None,
        entry_approval: bool | None = None,
        min_participants: int | None = None,
        submission_min: int | None = None,
        submission_max: int | None = None,
        submission_max_unlimited: bool | None = None,
        submission_overflow: bool | None = None,
        entry_mode: Literal["manual", "auto"] | None = None,
        submission_mode: Literal["manual", "semi_auto", "full_auto"] | None = None,
        selecting_mode: Literal["manual", "semi_auto", "full_auto"] | None = None,
        publish_mode: Literal["manual", "auto"] | None = None,
        result_mode: Literal["manual", "auto"] | None = None,
        author_publication_mode: Literal["with_result", "manual", "never"] | None = None,
        author_reveal: bool | None = None,
        author_reveal_zero: bool | None = None,
    ) -> None:
        assert interaction.guild is not None
        if select_rule_config is not None:
            if any(
                value is not None
                for value in (
                    title,
                    theme,
                    description,
                    entry_close_at,
                    submission_open_at,
                    submission_close_at,
                    selecting_close_at,
                    entry_approval,
                    min_participants,
                    submission_min,
                    submission_max,
                    submission_max_unlimited,
                    submission_overflow,
                    entry_mode,
                    submission_mode,
                    selecting_mode,
                    publish_mode,
                    result_mode,
                    author_publication_mode,
                    author_reveal,
                    author_reveal_zero,
                )
            ):
                await interaction.response.send_message(
                    embed=error_embed("select_rule_config は他の編集項目と同時指定できません。"),
                    ephemeral=True,
                )
                return
            if select_rule_config.strip().lower() in {"gui", "modal", "form", "フォーム", "入力"}:
                await interaction.response.send_modal(SelectRuleConfigModal(self, kukai_id))
                return
            await self._edit_select_rule_config(
                interaction,
                kukai_id=kukai_id,
                select_rule_config=select_rule_config,
            )
            return

        try:
            entry_close_dt = parse_datetime(entry_close_at) if entry_close_at else None
            submission_open_dt = parse_datetime(submission_open_at) if submission_open_at else None
            submission_close_dt = parse_datetime(submission_close_at) if submission_close_at else None
            selecting_close_dt = parse_datetime(selecting_close_at) if selecting_close_at else None
        except ValueError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        channel_rename_line: str | None = None
        channel_rename_warning: str | None = None

        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return

                mode_labels = {"manual": "手動", "semi_auto": "半自動", "full_auto": "全自動", "auto": "自動"}
                before = {
                    "title": kukai.title,
                    "theme": kukai.theme,
                    "description": kukai.description,
                    "entry_close_at": kukai.entry_close_at,
                    "entry_approval": kukai.entry_approval,
                    "min_participants": kukai.min_participants,
                    "submission_open_at": kukai.submission_open_at,
                    "submission_close_at": kukai.submission_close_at,
                    "selecting_close_at": kukai.selecting_close_at,
                    "submission_min": kukai.submission_min,
                    "submission_max": kukai.submission_max,
                    "submission_overflow": kukai.submission_overflow,
                    "entry_mode": kukai.entry_mode,
                    "submission_mode": kukai.submission_mode,
                    "selecting_mode": kukai.selecting_mode,
                    "publish_mode": kukai.publish_mode,
                    "result_mode": kukai.result_mode,
                    "author_publication_mode": kukai.author_publication_mode,
                    "author_reveal": kukai.author_reveal,
                    "author_reveal_zero": kukai.author_reveal_zero,
                }

                deadlines_changed = await kukai_service.edit_kukai(
                    session,
                    kukai,
                    title=title,
                    theme=theme,
                    description=description,
                    entry_close_at=entry_close_dt,
                    submission_open_at=submission_open_dt,
                    submission_close_at=submission_close_dt,
                    selecting_close_at=selecting_close_dt,
                    entry_approval=entry_approval,
                    min_participants=min_participants,
                    submission_min=submission_min,
                    submission_max=submission_max,
                    submission_max_unlimited=bool(submission_max_unlimited),
                    submission_overflow=submission_overflow,
                    entry_mode=entry_mode,
                    submission_mode=submission_mode,
                    selecting_mode=selecting_mode,
                    publish_mode=publish_mode,
                    result_mode=result_mode,
                    author_publication_mode=author_publication_mode,
                    author_reveal=author_reveal,
                    author_reveal_zero=author_reveal_zero,
                )

                if deadlines_changed:
                    await notification_service.cancel_kukai_jobs(session, kukai.id)
                    await notification_service.schedule_kukai_jobs(session, kukai)

                after = {
                    "title": kukai.title,
                    "theme": kukai.theme,
                    "description": kukai.description,
                    "entry_close_at": kukai.entry_close_at,
                    "entry_approval": kukai.entry_approval,
                    "min_participants": kukai.min_participants,
                    "submission_open_at": kukai.submission_open_at,
                    "submission_close_at": kukai.submission_close_at,
                    "selecting_close_at": kukai.selecting_close_at,
                    "submission_min": kukai.submission_min,
                    "submission_max": kukai.submission_max,
                    "submission_overflow": kukai.submission_overflow,
                    "entry_mode": kukai.entry_mode,
                    "submission_mode": kukai.submission_mode,
                    "selecting_mode": kukai.selecting_mode,
                    "publish_mode": kukai.publish_mode,
                    "result_mode": kukai.result_mode,
                    "author_publication_mode": kukai.author_publication_mode,
                    "author_reveal": kukai.author_reveal,
                    "author_reveal_zero": kukai.author_reveal_zero,
                }
                changed_lines: list[str] = []
                if before["title"] != after["title"]:
                    changed_lines.append(f"句会名: {before['title']} → {after['title']}")
                if before["theme"] != after["theme"]:
                    changed_lines.append(f"題: {(before['theme'] or '未設定')} → {(after['theme'] or '未設定')}")
                if before["description"] != after["description"]:
                    changed_lines.append("説明: 更新")
                if before["entry_approval"] != after["entry_approval"]:
                    changed_lines.append(
                        f"エントリー承認: {'あり' if before['entry_approval'] else 'なし'}"
                        f" → {'あり' if after['entry_approval'] else 'なし'}"
                    )
                if before["min_participants"] != after["min_participants"]:
                    changed_lines.append(f"最小成立人数: {before['min_participants']} → {after['min_participants']}")
                if before["submission_min"] != after["submission_min"]:
                    changed_lines.append(f"最小投句数: {before['submission_min']} → {after['submission_min']}")
                if before["submission_max"] != after["submission_max"]:
                    old_max = "∞" if before["submission_max"] is None else str(before["submission_max"])
                    new_max = "∞" if after["submission_max"] is None else str(after["submission_max"])
                    changed_lines.append(f"最大投句数: {old_max} → {new_max}")
                if before["submission_overflow"] != after["submission_overflow"]:
                    changed_lines.append(
                        f"投句数超過: {'許可' if before['submission_overflow'] else '不許可'}"
                        f" → {'許可' if after['submission_overflow'] else '不許可'}"
                    )
                if before["submission_mode"] != after["submission_mode"]:
                    changed_lines.append(
                        f"投句進行モード: {mode_labels.get(str(before['submission_mode']), str(before['submission_mode']))}"
                        f" → {mode_labels.get(str(after['submission_mode']), str(after['submission_mode']))}"
                    )
                if before["entry_mode"] != after["entry_mode"]:
                    changed_lines.append(
                        f"エントリー締切進行モード: {_entry_mode_label(str(before['entry_mode']))}"
                        f" → {_entry_mode_label(str(after['entry_mode']))}"
                    )
                if before["selecting_mode"] != after["selecting_mode"]:
                    changed_lines.append(
                        f"選句進行モード: {mode_labels.get(str(before['selecting_mode']), str(before['selecting_mode']))}"
                        f" → {mode_labels.get(str(after['selecting_mode']), str(after['selecting_mode']))}"
                    )
                if before["publish_mode"] != after["publish_mode"]:
                    changed_lines.append(
                        f"投句公開モード: {mode_labels.get(str(before['publish_mode']), str(before['publish_mode']))}"
                        f" → {mode_labels.get(str(after['publish_mode']), str(after['publish_mode']))}"
                    )
                if before["result_mode"] != after["result_mode"]:
                    changed_lines.append(
                        f"結果公開モード: {mode_labels.get(str(before['result_mode']), str(before['result_mode']))}"
                        f" → {mode_labels.get(str(after['result_mode']), str(after['result_mode']))}"
                    )
                if before["author_publication_mode"] != after["author_publication_mode"]:
                    changed_lines.append(
                        f"作者公開設定: {kukai_service.author_publication_label(str(before['author_publication_mode']))}"
                        f" → {kukai_service.author_publication_label(str(after['author_publication_mode']))}"
                    )
                if before["author_reveal"] != after["author_reveal"]:
                    changed_lines.append(
                        f"作者公開: {'公開' if before['author_reveal'] else '非公開'} → {'公開' if after['author_reveal'] else '非公開'}"
                    )
                if before["author_reveal_zero"] != after["author_reveal_zero"]:
                    changed_lines.append(
                        f"0点以下作者公開: {'公開' if before['author_reveal_zero'] else '非公開'}"
                        f" → {'公開' if after['author_reveal_zero'] else '非公開'}"
                    )
                if before["entry_close_at"] != after["entry_close_at"]:
                    changed_lines.append(
                        f"エントリー締切: {format_jst(before['entry_close_at']) if before['entry_close_at'] else '未設定'}"
                        f" → {format_jst(after['entry_close_at']) if after['entry_close_at'] else '未設定'}"
                    )
                if before["submission_close_at"] != after["submission_close_at"]:
                    changed_lines.append(
                        f"投句締切: {format_jst(before['submission_close_at']) if before['submission_close_at'] else '未設定'}"
                        f" → {format_jst(after['submission_close_at']) if after['submission_close_at'] else '未設定'}"
                    )
                if before["submission_open_at"] != after["submission_open_at"]:
                    changed_lines.append(
                        f"投句開始: {format_jst(before['submission_open_at']) if before['submission_open_at'] else '未設定'}"
                        f" → {format_jst(after['submission_open_at']) if after['submission_open_at'] else '未設定'}"
                    )
                if before["selecting_close_at"] != after["selecting_close_at"]:
                    changed_lines.append(
                        f"選句締切: {format_jst(before['selecting_close_at']) if before['selecting_close_at'] else '未設定'}"
                        f" → {format_jst(after['selecting_close_at']) if after['selecting_close_at'] else '未設定'}"
                    )

            if before["title"] != after["title"] and kukai.channel_id:
                channel = interaction.guild.get_channel(kukai.channel_id)
                old_channel_name = _sanitize_channel_name(str(before["title"]))
                new_channel_name = _sanitize_channel_name(str(after["title"]))
                if (
                    isinstance(channel, discord.TextChannel)
                    and channel.name.lower() == old_channel_name.lower()
                    and channel.name.lower() != new_channel_name.lower()
                ):
                    try:
                        await channel.edit(
                            name=new_channel_name,
                            reason="句会名の更新に合わせてチャンネル名を更新",
                        )
                        channel_rename_line = f"チャンネル名: {old_channel_name} → {new_channel_name}"
                    except discord.Forbidden:
                        channel_rename_warning = "チャンネル名を更新する権限がありません。"
                    except discord.HTTPException:
                        channel_rename_warning = "チャンネル名の更新に失敗しました。"

            if channel_rename_line:
                changed_lines.append(channel_rename_line)

            extra_parts: list[str] = []
            if deadlines_changed:
                extra_parts.append("締切変更に合わせて通知ジョブを再登録しました。")
            if channel_rename_line:
                extra_parts.append("チャンネル名も更新しました。")
            if channel_rename_warning:
                extra_parts.append(f"⚠️ {channel_rename_warning}")
            extra = ("\n" + "\n".join(extra_parts)) if extra_parts else ""
            await interaction.response.send_message(
                embed=success_embed(f"句会「{kukai.title}」の設定を更新しました。{extra}"),
                ephemeral=True,
            )
            await self._announce_settings_updated(
                interaction.guild,
                kukai,
                deadlines_changed=deadlines_changed,
                changed_lines=changed_lines,
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    async def _edit_select_rule_config(
        self,
        interaction: discord.Interaction,
        *,
        kukai_id: int | None,
        select_rule_config: str,
    ) -> None:
        try:
            fields = parse_fields(select_rule_config)
            reject_unknown_keys(fields, {"preset_id", "points_enabled", "label"})
            preset_id_raw = first_value(fields, "preset_id")
            label_fields = values_for(fields, "label")
            if preset_id_raw and label_fields:
                raise BulkParseError("preset_id と label は同時指定できません。")
            if not preset_id_raw and not label_fields:
                raise BulkParseError("preset_id または label を1件以上指定してください。")
            if preset_id_raw and first_value(fields, "points_enabled") is not None:
                raise BulkParseError("preset_id 指定時は points_enabled を直接指定できません。")
            preset_id = parse_int(preset_id_raw, name="preset_id", min_value=1) if preset_id_raw else None
            points_enabled = parse_bool(
                first_value(fields, "points_enabled", "true") or "true",
                name="points_enabled",
            )
            label_specs = [
                parse_label_spec(field.value, line_no=field.line_no)
                for field in label_fields
            ]
        except BulkParseError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        clear_existing_select_data = False
        select_count = 0
        overall_count = 0
        old_summary = "未設定"
        new_summary = "未設定"

        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.edit_original_response(
                        embed=error_embed("この操作は句会管理者のみ実行できます。")
                    )
                    return

                state = KukaiState.from_value(kukai.state)
                if state not in {
                    KukaiState.DRAFT,
                    KukaiState.ENTRY_OPEN,
                    KukaiState.ENTRY_CLOSED,
                    KukaiState.SUBMISSION_OPEN,
                    KukaiState.SUBMISSION_CLOSED,
                    KukaiState.WAITING_PUBLISH,
                }:
                    await interaction.edit_original_response(
                        embed=error_embed("選句開始後は選句ルールを差し替えできません。")
                    )
                    return

                from sqlalchemy import select as _sa_select
                from bot.models.select_rule import SelectLabel

                current_labels = list(
                    (
                        await session.execute(
                            _sa_select(SelectLabel)
                            .where(SelectLabel.kukai_id == kukai.id)
                            .order_by(SelectLabel.display_order)
                        )
                    ).scalars().all()
                )
                old_summary = build_select_summary(kukai.submission_min, kukai.submission_max, current_labels)

                if preset_id is not None:
                    template = await select_rule_service.get_template(
                        session, interaction.guild.id, preset_id
                    )
                    points_enabled, _ = select_rule_service.deserialize_template_payload(
                        template.definition_json
                    )
                    label_specs = select_rule_service.build_kukai_specs_from_template(template)

                normalized_specs = select_rule_service.normalize_kukai_specs(label_specs)
                if not points_enabled:
                    for spec in normalized_specs:
                        spec["point"] = 0
                new_summary = build_select_summary(kukai.submission_min, kukai.submission_max, normalized_specs)
                select_count, overall_count = await kukai_service.count_select_rule_data(session, kukai.id)
                resolved_id = kukai.id

            if select_count or overall_count:
                view = ConfirmView(timeout=60)
                if view.children:
                    view.children[0].label = "削除して差し替え"  # type: ignore[attr-defined]
                warning = discord.Embed(
                    title="選句データが残っています",
                    description=(
                        "選句ルールを差し替えるには、既存の選句・選評データを削除します。\n"
                        f"選句: {select_count} 件 / 総評: {overall_count} 件"
                    ),
                    color=COLOR_INFO,
                )
                warning.add_field(name="現在", value=old_summary, inline=False)
                warning.add_field(name="差し替え後", value=new_summary, inline=False)
                warning.set_footer(text=f"句会ID: {resolved_id}")
                await interaction.edit_original_response(embed=warning, view=view)
                await view.wait()
                if not view.confirmed:
                    await interaction.edit_original_response(
                        embed=success_embed("選句ルールの差し替えをキャンセルしました。"),
                        view=None,
                    )
                    return
                clear_existing_select_data = True

            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, resolved_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.edit_original_response(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"),
                        view=None,
                    )
                    return
                labels = await kukai_service.replace_select_rules(
                    session,
                    kukai,
                    select_label_specs=label_specs,
                    points_enabled=points_enabled,
                    clear_existing_select_data=clear_existing_select_data,
                )
                final_summary = build_select_summary(kukai.submission_min, kukai.submission_max, labels)
                changed_lines = [f"選句ルール: {old_summary} → {final_summary}"]
                if clear_existing_select_data:
                    changed_lines.append(f"選句・選評データを削除: 選句 {select_count} 件 / 総評 {overall_count} 件")

            await interaction.edit_original_response(
                embed=success_embed(
                    f"句会「{kukai.title}」の選句ルールを差し替えました。\n"
                    f"新しい設定: {final_summary}"
                ),
                view=None,
            )
            await self._announce_settings_updated(
                interaction.guild,
                kukai,
                deadlines_changed=False,
                changed_lines=changed_lines,
            )
        except ServiceError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)), view=None)

    # ------------------------------------------------------------------
    # /kukai admin subgroup
    # ------------------------------------------------------------------

    @kukai_admin_grp.command(name="status", description="【句会管理者】エントリー・投句・選句の進捗を確認します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def admin_status(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        from collections import Counter, defaultdict
        from sqlalchemy import select as _sa_select
        from bot.models.select_rule import SelectLabel
        from bot.repositories import entry_repo, select_repo, submission_repo
        from bot.utils.text import discord_safe

        def _entry_status_icon(status: str) -> str:
            return {"approved": "✅", "pending": "⏳", "rejected": "❌", "withdrawn": "↩️"}.get(status, "•")

        def _entry_status_label(status: str) -> str:
            return {"approved": "承認済", "pending": "承認待ち", "rejected": "却下", "withdrawn": "取消"}.get(status, status)

        def _member_name(guild: discord.Guild, user_id: int, haigo: str | None = None) -> str:
            if haigo:
                return discord_safe(haigo)
            member = guild.get_member(user_id)
            return discord_safe(member.display_name if member else f"UID:{user_id}")

        def _field_value(lines: list[str], *, limit: int = 1024) -> str:
            if not lines:
                return "（なし）"
            value = ""
            shown = 0
            for line in lines:
                candidate = f"{value}\n{line}" if value else line
                if len(candidate) > limit:
                    remaining = len(lines) - shown
                    suffix = f"\n…他 {remaining} 件"
                    if value and len(value) + len(suffix) <= limit:
                        value += suffix
                    break
                value = candidate
                shown += 1
            return value or "（表示できる項目がありません）"

        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この句会の管理者権限がありません。"), ephemeral=True
                    )
                    return
                entries = await entry_repo.list_by_kukai(session, kukai.id)
                submissions = await submission_repo.list_by_kukai(session, kukai.id)
                selects = await select_repo.get_all_selects(session, kukai.id)
                overall_comments = await select_repo.list_overall_comments(session, kukai.id)
                label_result = await session.execute(
                    _sa_select(SelectLabel)
                    .where(SelectLabel.kukai_id == kukai.id)
                    .order_by(SelectLabel.display_order)
                )
                labels = list(label_result.scalars().all())
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return

        guild = interaction.guild
        entry_by_user = {entry.user_id: entry for entry in entries}
        approved_entries = [entry for entry in entries if entry.status == "approved"]
        if kukai.entry_enabled:
            participant_user_ids = [entry.user_id for entry in approved_entries]
        else:
            participant_user_ids = sorted(
                {row.user_id for row in submissions}
                | {row.selector_user_id for row in selects}
                | {row.user_id for row in overall_comments}
            )
        submission_counts = Counter(row.user_id for row in submissions)
        selects_by_user: dict[int, list] = defaultdict(list)
        for row in selects:
            selects_by_user[row.selector_user_id].append(row)
        overall_user_ids = {row.user_id for row in overall_comments}
        non_author_labels = [label for label in labels if label.label != "作者コメント"]

        embed = discord.Embed(title=f"管理者用 進捗確認 — {kukai.title}", color=COLOR_INFO)
        embed.set_footer(text=f"句会ID: {kukai.id} | 状態: {kukai.state}")
        if kukai.entry_enabled:
            entry_lines = [
                f"{_entry_status_icon(e.status)} {_member_name(guild, e.user_id, e.haigo)} ({_entry_status_label(e.status)})"
                for e in entries
            ]
            embed.add_field(
                name=f"エントリー者 ({len(entries)}件 / 承認済 {len(approved_entries)}件)",
                value=_field_value(entry_lines), inline=False,
            )
        else:
            embed.add_field(name="エントリー者", value="エントリー制なし。", inline=False)
        max_label = "∞" if kukai.submission_max is None else str(kukai.submission_max)
        submission_lines = [
            f"{'✅' if submission_counts.get(u, 0) >= kukai.submission_min else '⚠️'} "
            f"{_member_name(guild, u, entry_by_user.get(u).haigo if entry_by_user.get(u) else None)} "
            f"{submission_counts.get(u, 0)}句投句済（必要 {kukai.submission_min}〜{max_label}句）"
            for u in participant_user_ids
        ]
        embed.add_field(name="投句状況", value=_field_value(submission_lines), inline=False)
        selection_lines = []
        for user_id in participant_user_ids:
            entry = entry_by_user.get(user_id)
            user_selects = selects_by_user.get(user_id, [])
            label_counts = Counter(row.select_label_id for row in user_selects if not row.is_self_comment)
            missing = [lbl for lbl in non_author_labels if label_counts.get(lbl.id, 0) < lbl.min_count]
            icon = "✅" if not missing else "⚠️"
            parts = [f"{lbl.label}{label_counts.get(lbl.id, 0)}" for lbl in non_author_labels]
            comment_count = sum(1 for row in user_selects if not row.is_self_comment and row.comment is not None)
            author_comment_count = sum(1 for row in user_selects if row.is_self_comment)
            parts.append(f"コメント{comment_count}")
            if author_comment_count:
                parts.append(f"作者コメント{author_comment_count}")
            parts.append(f"総評{1 if user_id in overall_user_ids else 0}")
            missing_text = (
                " 不足:" + ",".join(f"{lbl.label}{label_counts.get(lbl.id, 0)}/{lbl.min_count}" for lbl in missing)
                if missing else ""
            )
            selection_lines.append(
                f"{icon} {_member_name(guild, user_id, entry.haigo if entry else None)} "
                f"{' '.join(parts)}{missing_text}"
            )
        if not non_author_labels:
            selection_lines = ["（作者コメント以外の選句ラベルがありません）"]
        embed.add_field(name="選句状況", value=_field_value(selection_lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @kukai_admin_grp.command(name="add", description="【句会管理者】句会管理者を追加します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）", user="追加するユーザー")
    async def admin_add(
        self, interaction: discord.Interaction, user: discord.Member, kukai_id: int | None = None
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
                allowed = (
                    interaction.user.id == interaction.guild.owner_id
                    or await permission_service.is_kukai_admin(session, kukai, interaction.user)  # type: ignore[arg-type]
                )
                if not allowed:
                    await interaction.response.send_message(
                        embed=error_embed("管理者追加は句会管理者またはサーバー所有者のみ実行できます。"), ephemeral=True
                    )
                    return
                await kukai_service.add_kukai_admin(session, kukai, user_id=user.id, added_by=interaction.user.id)
            await interaction.response.send_message(
                embed=success_embed(f"{user.mention} を句会管理者に追加しました。"), ephemeral=True
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai_admin_grp.command(name="remove", description="【句会管理者】句会管理者を削除します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）", user="削除するユーザー")
    async def admin_remove(
        self, interaction: discord.Interaction, user: discord.Member, kukai_id: int | None = None
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
                allowed = (
                    interaction.user.id == interaction.guild.owner_id
                    or interaction.user.id == kukai.created_by
                )
                if not allowed:
                    await interaction.response.send_message(
                        embed=error_embed("管理者削除は句会作成者またはサーバー所有者のみ実行できます。"), ephemeral=True
                    )
                    return
                await kukai_service.remove_kukai_admin(session, kukai, user_id=user.id)
            await interaction.response.send_message(
                embed=success_embed(f"{user.mention} を句会管理者から削除しました。"), ephemeral=True
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai_admin_grp.command(name="export", description="【句会管理者】句会データをエクスポートします")
    @app_commands.describe(kukai_id="句会ID（省略時は全句会 / サーバー管理者のみ）", export_format="出力形式")
    async def admin_export(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
        export_format: Literal["json", "csv"] = "json",
    ) -> None:
        assert interaction.guild is not None
        from bot.services import export_service

        def _is_owner_or_admin(member: discord.Member) -> bool:
            return member.id == member.guild.owner_id or member.guild_permissions.administrator

        try:
            async with get_session() as session:
                if kukai_id is None:
                    if not _is_owner_or_admin(interaction.user):  # type: ignore[arg-type]
                        await interaction.response.send_message(
                            embed=error_embed("全句会エクスポートはサーバー管理者のみ実行できます。"), ephemeral=True
                        )
                        return
                else:
                    kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                    if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                        await interaction.response.send_message(
                            embed=error_embed("この句会の管理者権限がありません。"), ephemeral=True
                        )
                        return
                payload = await export_service.export_payload(
                    session, guild_id=interaction.guild.id, kukai_id=kukai_id
                )
            import io
            if export_format == "csv":
                content = export_service.payload_to_csv(payload).encode("utf-8")
                filename = "kukai_export.csv"
            else:
                content = export_service.payload_to_json(payload).encode("utf-8")
                filename = "kukai_export.json"
            await interaction.user.send(
                content=f"句会データを送付します（{export_format.upper()}）",
                file=discord.File(io.BytesIO(content), filename=filename),
            )
            await interaction.response.send_message(
                embed=success_embed("DMにエクスポートファイルを送付しました。"), ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("DMを送信できませんでした。DM受信設定を確認してください。"), ephemeral=True
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    @kukai_admin_grp.command(name="import", description="【サーバー管理者】句会データ(JSON)をインポートします")
    @app_commands.describe(file="exportで出力したJSONファイル")
    async def admin_import(self, interaction: discord.Interaction, file: discord.Attachment) -> None:
        assert interaction.guild is not None
        import json
        from bot.services import export_service
        from bot.services.errors import ValidationError

        def _is_owner_or_admin(member: discord.Member) -> bool:
            return member.id == member.guild.owner_id or member.guild_permissions.administrator

        if not _is_owner_or_admin(interaction.user):  # type: ignore[arg-type]
            await interaction.response.send_message(
                embed=error_embed("インポートはサーバー管理者のみ実行できます。"), ephemeral=True
            )
            return
        try:
            max_bytes = export_service.MAX_IMPORT_FILE_BYTES
            if file.size > max_bytes:
                raise ValidationError(f"インポートJSONは{max_bytes // (1024 * 1024)}MB以内にしてください。")
            raw = (await file.read()).decode("utf-8")
            if len(raw.encode("utf-8")) > max_bytes:
                raise ValidationError(f"インポートJSONは{max_bytes // (1024 * 1024)}MB以内にしてください。")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValidationError("JSON形式が不正です。")
            async with get_session() as session:
                imported_ids = await export_service.import_payload(
                    session, guild_id=interaction.guild.id, payload=payload
                )
                for imported_id in imported_ids:
                    kukai = await kukai_service.get_kukai(session, imported_id, interaction.guild.id)
                    await notification_service.schedule_kukai_jobs(session, kukai)
            await interaction.response.send_message(
                embed=success_embed(
                    f"{len(imported_ids)}件の句会をインポートしました。\n"
                    f"句会ID: {', '.join(str(k) for k in imported_ids)}"
                ),
                ephemeral=True,
            )
        except UnicodeDecodeError:
            await interaction.response.send_message(
                embed=error_embed("UTF-8のJSONファイルを指定してください。"), ephemeral=True
            )
        except json.JSONDecodeError:
            await interaction.response.send_message(
                embed=error_embed("JSONのパースに失敗しました。ファイル内容を確認してください。"), ephemeral=True
            )
        except ServiceError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)

    # ------------------------------------------------------------------
    # /kukai notify subgroup
    # ------------------------------------------------------------------

    _EVENT_LABELS = {
        "entry_close": "エントリー締切",
        "submission_open": "投句開始",
        "submission_close": "投句締切",
        "selecting_close": "選句締切",
        "voice_start": "ボイス句会開始",
    }

    @kukai_notify_grp.command(name="list", description="句会の通知設定を表示します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def notify_list(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        from sqlalchemy import select as _sa_select
        from bot.models.notification import NotificationSchedule

        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                schedules = (
                    await session.execute(
                        _sa_select(NotificationSchedule)
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
            def _fmt_offset(s: int) -> str:
                if s % 3600 == 0:
                    return f"{s // 3600}h"
                if s % 60 == 0:
                    return f"{s // 60}m"
                return f"{s}s"

            def _fmt_dest(ch_id: int | None, mention: bool) -> str:
                if ch_id == -2:
                    return "管理者スレッド" + (" + mention" if mention else "")
                if ch_id == -1:
                    return "DM"
                base = "句会チャンネル" if ch_id is None else f"<#{ch_id}>"
                return base + (" + mention" if mention else "")

            lines = [
                f"[{row.id}] {self._EVENT_LABELS.get(row.event_type, row.event_type)} "
                f"{_fmt_offset(row.offset_secs)}前 / "
                f"{_fmt_dest(row.channel_id, row.mention)} / "
                f"{row.target}{' / 送信済み' if row.fired else ''}"
                for row in schedules
            ]
            embed.description = "\n".join(lines[:20])
            if len(schedules) > 20:
                embed.set_footer(text=f"他 {len(schedules) - 20} 件")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @kukai_notify_grp.command(name="replace", description="【句会管理者】通知設定を一括で差し替えます")
    @app_commands.describe(
        kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）",
        config="1行1件: event,offset,destination,target,mention",
    )
    async def notify_replace(
        self, interaction: discord.Interaction, config: str, kukai_id: int | None = None
    ) -> None:
        from bot.utils.bulk_parser import BulkParseError, parse_reminder_spec

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
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.edit_original_response(
                        embed=error_embed("この操作は句会管理者のみ実行できます。")
                    )
                    return
                from sqlalchemy import select as _sa_select
                from bot.models.voice_session import VoiceSession
                voice_session = (
                    await session.execute(_sa_select(VoiceSession).where(VoiceSession.kukai_id == kukai.id))
                ).scalar_one_or_none()
                kukai.__dict__["voice_session"] = voice_session
                await notification_service.cancel_kukai_jobs(session, kukai.id)
                await notification_service.replace_notification_schedules(session, kukai, specs)
                await notification_service.schedule_kukai_jobs(session, kukai)
        except ServiceError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)))
            return
        await interaction.edit_original_response(
            embed=success_embed(f"句会 `{kukai.id}` の通知設定を {len(specs)} 件に差し替えました。")
        )

    @kukai_notify_grp.command(name="restore", description="【句会管理者】通知設定をデフォルトに戻します")
    @app_commands.describe(kukai_id="句会ID（省略可: このチャンネルで1件なら自動特定）")
    async def notify_restore(self, interaction: discord.Interaction, kukai_id: int | None = None) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=effective_channel_id(interaction),
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.edit_original_response(
                        embed=error_embed("この操作は句会管理者のみ実行できます。")
                    )
                    return
                from sqlalchemy import select as _sa_select
                from bot.models.voice_session import VoiceSession
                voice_session = (
                    await session.execute(_sa_select(VoiceSession).where(VoiceSession.kukai_id == kukai.id))
                ).scalar_one_or_none()
                kukai.__dict__["voice_session"] = voice_session
                await notification_service.cancel_kukai_jobs(session, kukai.id)
                await notification_service.replace_notification_schedules(session, kukai, [])
                await notification_service.schedule_kukai_jobs(session, kukai)
        except ServiceError as e:
            await interaction.edit_original_response(embed=error_embed(str(e)))
            return
        await interaction.edit_original_response(
            embed=success_embed(f"句会 `{kukai.id}` の通知設定をデフォルトに戻しました。")
        )


def _sanitize_channel_name(title: str) -> str:
    name = title.replace(" ", "-").replace("　", "-")
    name = re.sub(r'[<>"\'\\|]', "", name)
    return name[:100].strip("-") or "kukai"


def _build_info_embed(
    kukai,
    *,
    select_labels: list | None = None,
    voice_session=None,
) -> discord.Embed:
    state_ja = STATE_LABEL.get(kukai.state, kukai.state)
    embed = discord.Embed(
        title=f"📋 {kukai.title}",
        description=kukai.description or "",
        color=COLOR_RESULT if kukai.state == "results" else COLOR_INFO,
    )
    if kukai.theme:
        embed.add_field(name="題", value=kukai.theme, inline=True)
    embed.add_field(name="現在の状態", value=state_ja, inline=True)

    if select_labels is not None:
        summary = build_select_summary(kukai.submission_min, kukai.submission_max, select_labels)
        embed.add_field(name="句数", value=summary, inline=False)

    if kukai.entry_enabled:
        entry_deadline = format_jst(kukai.entry_close_at) if kukai.entry_close_at else "未定"
        embed.add_field(
            name=f"エントリー締切（{_entry_mode_label(getattr(kukai, 'entry_mode', 'manual'))}）",
            value=entry_deadline,
            inline=False,
        )

    submission_deadline = format_jst(kukai.submission_close_at) if kukai.submission_close_at else "未定"
    selecting_deadline = format_jst(kukai.selecting_close_at) if kukai.selecting_close_at else "未定"
    if getattr(kukai, "submission_open_at", None):
        embed.add_field(
            name="投句開始",
            value=format_jst(kukai.submission_open_at),
            inline=False,
        )
    embed.add_field(
        name=f"投句締切（{_mode_label(getattr(kukai, 'submission_mode', 'manual'))}）",
        value=submission_deadline,
        inline=False,
    )
    embed.add_field(
        name=f"選句締切（{_mode_label(getattr(kukai, 'selecting_mode', 'manual'))}）",
        value=selecting_deadline,
        inline=False,
    )
    author_mode = getattr(kukai, "author_publication_mode", "with_result")
    if author_mode == "never":
        author_value = "作者公開はしない"
    elif getattr(kukai, "author_reveal", False):
        author_value = f"{kukai_service.author_publication_label(author_mode)}（公開済み）"
    else:
        author_value = f"{kukai_service.author_publication_label(author_mode)}（未公開）"
    if author_mode == "never":
        zero_value = "適用外"
    else:
        zero_value = "公開" if getattr(kukai, "author_reveal_zero", True) else "非公開"
    embed.add_field(
        name="作者公開設定",
        value=f"{author_value}\n0点以下作者: {zero_value}",
        inline=False,
    )

    if voice_session is None:
        voice_session = getattr(kukai, "__dict__", {}).get("voice_session")
    if voice_session is not None:
        start_at = format_jst(voice_session.start_at) if voice_session.start_at else "未定"
        voice_value = f"開始: {start_at}\n場所: <#{voice_session.vc_channel_id}>"
        if voice_session.end_at is not None:
            voice_value += f"\n終了: {format_jst(voice_session.end_at)}"
        embed.add_field(name="ボイス句会", value=voice_value, inline=False)

    embed.set_footer(text=f"句会ID: {kukai.id}")
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(KukaiCog(bot))


# ---------------------------------------------------------------------------
# Helpers shared by admin/notify subgroup methods (imported at call site)
# ---------------------------------------------------------------------------

def _format_offset(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


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


async def _approved_entry_lines(*, interaction_guild: discord.Guild, kukai_id: int) -> list[str]:
    from sqlalchemy import select as _sa_select

    from bot.models.entry import Entry
    from bot.utils.text import discord_safe

    async with get_session() as session:
        result = await session.execute(
            _sa_select(Entry)
            .where(Entry.kukai_id == kukai_id, Entry.status == "approved")
            .order_by(Entry.created_at)
        )
        entries = list(result.scalars().all())

    lines: list[str] = []
    for entry in entries:
        member = interaction_guild.get_member(entry.user_id)
        name = entry.haigo or (member.display_name if member else f"UID:{entry.user_id}")
        lines.append(discord_safe(name))
    return lines


def _format_destination(channel_id: int | None, mention: bool) -> str:
    if channel_id == -2:
        return "管理者スレッド" + (" + mention" if mention else "")
    if channel_id == -1:
        return "DM"
    base = "句会チャンネル" if channel_id is None else f"<#{channel_id}>"
    if mention:
        base += " + mention"
    return base
