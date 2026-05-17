"""Kukai management commands: /kukai *"""

import asyncio
import re

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
    "waiting_results": "結果公開待ち",
    "results": "結果公開中",
    "ended": "終了",
    "paused": "一時停止",
    "cancelled": "中止",
}

ROLLBACK_TARGET_CHOICES = [
    app_commands.Choice(name="下書き", value=KukaiState.DRAFT.value),
    app_commands.Choice(name="エントリー受付中", value=KukaiState.ENTRY_OPEN.value),
    app_commands.Choice(name="エントリー締切", value=KukaiState.ENTRY_CLOSED.value),
    app_commands.Choice(name="投句受付中", value=KukaiState.SUBMISSION_OPEN.value),
    app_commands.Choice(name="投句締切", value=KukaiState.SUBMISSION_CLOSED.value),
    app_commands.Choice(name="投句公開待ち", value=KukaiState.WAITING_PUBLISH.value),
    app_commands.Choice(name="選句受付中", value=KukaiState.SELECTING_OPEN.value),
    app_commands.Choice(name="選句締切", value=KukaiState.SELECTING_CLOSED.value),
]


class StageActionView(discord.ui.View):
    def __init__(self, kukai_id: int, state: KukaiState) -> None:
        super().__init__(timeout=86400)
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
        button = discord.ui.Button(label=button_label, style=discord.ButtonStyle.primary, row=0)

        async def _callback(interaction: discord.Interaction) -> None:
            assert interaction.guild is not None
            try:
                async with get_session() as session:
                    kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                    current = KukaiState.from_value(kukai.state)

                    if self.state == KukaiState.ENTRY_OPEN:
                        if current != KukaiState.ENTRY_OPEN:
                            await interaction.response.send_message(
                                embed=error_embed("現在はエントリー受付中ではありません。"),
                                ephemeral=True,
                            )
                            return
                        from bot.cogs.entry_cog import EntryHaigoModal

                        await interaction.response.send_modal(EntryHaigoModal(kukai.id))
                        return

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
            from sqlalchemy import select as _sa_select
            from sqlalchemy.orm import selectinload
            from bot.models.kukai import Kukai as _Kukai
            async with get_session() as session:
                result = await session.execute(
                    _sa_select(_Kukai)
                    .where(_Kukai.id == kukai_id, _Kukai.guild_id == interaction.guild.id)
                    .options(selectinload(_Kukai.select_labels))
                )
                kukai = result.scalar_one_or_none()
                if kukai is None:
                    await interaction.response.send_message(
                        embed=error_embed(f"句会 ID {kukai_id} が見つかりません。"), ephemeral=True
                    )
                    return
                select_labels = list(kukai.select_labels)
            embed = _build_info_embed(kukai, select_labels=select_labels)
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

    @kukai.command(name="create_bulk", description="【管理者】行形式で新しい句会を一括作成します")
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
                    "min_participants",
                    "entry_close_at",
                    "submission_close_at",
                    "selecting_close_at",
                    "submission_min",
                    "submission_max",
                    "submission_overflow",
                    "submission_mode",
                    "selecting_mode",
                    "publish_mode",
                    "result_mode",
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
                if not entry_close_raw:
                    raise BulkParseError("entry_enabled=true の場合 entry_close_at は必須です。")
                entry_close_at = parse_datetime_field(entry_close_raw, name="entry_close_at")

            submission_close_at = parse_datetime_field(submission_close_raw, name="submission_close_at")
            selecting_close_at = parse_datetime_field(selecting_close_raw, name="selecting_close_at")
            submission_min = parse_int(
                first_value(fields, "submission_min", "1") or "1",
                name="submission_min",
                min_value=1,
            )
            submission_max = parse_optional_int(
                first_value(fields, "submission_max", "3") or "3",
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
            for name, value, allowed in (
                ("submission_mode", submission_mode, {"manual", "semi_auto", "full_auto"}),
                ("selecting_mode", selecting_mode, {"manual", "semi_auto", "full_auto"}),
                ("publish_mode", publish_mode, {"manual", "auto"}),
                ("result_mode", result_mode, {"manual", "auto"}),
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
                    submission_close_at=submission_close_at,
                    selecting_close_at=selecting_close_at,
                    entry_enabled=entry_enabled,
                    entry_approval=parse_bool(
                        first_value(fields, "entry_approval", "false") or "false",
                        name="entry_approval",
                    ),
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
                    author_reveal=parse_bool(
                        first_value(fields, "author_reveal", "true") or "true",
                        name="author_reveal",
                    ),
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
            info.add_field(name="エントリー締切", value=format_jst(entry_close_at), inline=False)
        info.add_field(name="投句締切", value=format_jst(submission_close_at), inline=False)
        info.add_field(name="選句締切", value=format_jst(selecting_close_at), inline=False)
        if voice_enabled and voice_channel_id is not None and voice_start_at is not None:
            voice_value = f"開始: {format_jst(voice_start_at)}\n場所: <#{voice_channel_id}>"
            if voice_end_at is not None:
                voice_value += f"\n終了: {format_jst(voice_end_at)}"
            info.add_field(name="ボイス句会", value=voice_value, inline=False)
        info.set_footer(text=f"句会ID: {kukai_id}")
        try:
            await channel.send(embed=info)
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
                await notification_service.schedule_kukai_jobs(session, kukai)
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
            if view.children:
                await send_with_retry(lambda: channel.send(embed=embed, view=view))
            else:
                await send_with_retry(lambda: channel.send(embed=embed))
        except Exception:
            pass

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
        overall_comments = await select_repo.list_overall_comments(session, kukai.id)
        from bot.cogs.result_cog import ResultSwitchView, _resolve_initial_format

        view = ResultSwitchView(
            kukai,
            results,
            overall_comments,
            guild,
            initial_format=_resolve_initial_format(kukai, None),
        )
        first_message_id: int | None = None
        try:
            sent = await send_with_retry(lambda: channel.send(embed=view.current_embed(), view=view))
            first_message_id = sent.id
        except discord.Forbidden:
            return len(results), "公開チャンネルへの送信権限がないため、結果を投稿できません。", None

        return len(results), None, first_message_id

    @kukai.command(name="rollback", description="【管理者】句会を指定した前段階へ戻します")
    @app_commands.describe(kukai_id="句会ID", target_state="戻す先の状態")
    @app_commands.choices(target_state=ROLLBACK_TARGET_CHOICES)
    async def kukai_rollback(
        self,
        interaction: discord.Interaction,
        kukai_id: int,
        target_state: str,
    ) -> None:
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
                if not await permission_service.is_kukai_admin(session, kukai, interaction.user):  # type: ignore[arg-type]
                    await interaction.response.send_message(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"), ephemeral=True
                    )
                    return
                target = KukaiState.from_value(target_state)
                current = KukaiState.from_value(kukai.state)
                try:
                    submission_service.validate_rollback_target(current, target)
                except ServiceError as e:
                    await interaction.response.send_message(
                        embed=error_embed(str(e)), ephemeral=True
                    )
                    return
                allow_reset_submissions = submission_service.can_reset_submissions_on_rollback(target)

            current_label = STATE_LABEL.get(current.value, current.value)
            target_label = STATE_LABEL.get(target.value, target.value)
            reset_note = (
                "投句内容をリセットする選択もできます。"
                if allow_reset_submissions
                else "この戻し先では投句内容は保持されます。"
            )
            view = RollbackView(allow_reset_submissions=allow_reset_submissions)
            await interaction.response.send_message(
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
                ephemeral=True,
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
                kukai = await kukai_service.get_kukai(session, kukai_id, interaction.guild.id)
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

                mode_labels = {"manual": "手動", "semi_auto": "半自動", "full_auto": "全自動", "auto": "自動"}
                before = {
                    "title": kukai.title,
                    "theme": kukai.theme,
                    "description": kukai.description,
                    "submission_close_at": kukai.submission_close_at,
                    "selecting_close_at": kukai.selecting_close_at,
                    "submission_min": kukai.submission_min,
                    "submission_max": kukai.submission_max,
                    "submission_mode": kukai.submission_mode,
                    "selecting_mode": kukai.selecting_mode,
                    "publish_mode": kukai.publish_mode,
                    "result_mode": kukai.result_mode,
                    "author_reveal": kukai.author_reveal,
                    "author_reveal_zero": kukai.author_reveal_zero,
                }

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

                after = {
                    "title": kukai.title,
                    "theme": kukai.theme,
                    "description": kukai.description,
                    "submission_close_at": kukai.submission_close_at,
                    "selecting_close_at": kukai.selecting_close_at,
                    "submission_min": kukai.submission_min,
                    "submission_max": kukai.submission_max,
                    "submission_mode": kukai.submission_mode,
                    "selecting_mode": kukai.selecting_mode,
                    "publish_mode": kukai.publish_mode,
                    "result_mode": kukai.result_mode,
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
                if before["submission_min"] != after["submission_min"]:
                    changed_lines.append(f"最小投句数: {before['submission_min']} → {after['submission_min']}")
                if before["submission_max"] != after["submission_max"]:
                    old_max = "∞" if before["submission_max"] is None else str(before["submission_max"])
                    new_max = "∞" if after["submission_max"] is None else str(after["submission_max"])
                    changed_lines.append(f"最大投句数: {old_max} → {new_max}")
                if before["submission_mode"] != after["submission_mode"]:
                    changed_lines.append(
                        f"投句進行モード: {mode_labels.get(str(before['submission_mode']), str(before['submission_mode']))}"
                        f" → {mode_labels.get(str(after['submission_mode']), str(after['submission_mode']))}"
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
                if before["author_reveal"] != after["author_reveal"]:
                    changed_lines.append(
                        f"作者公開: {'公開' if before['author_reveal'] else '非公開'} → {'公開' if after['author_reveal'] else '非公開'}"
                    )
                if before["author_reveal_zero"] != after["author_reveal_zero"]:
                    changed_lines.append(
                        f"0点以下作者公開: {'公開' if before['author_reveal_zero'] else '非公開'}"
                        f" → {'公開' if after['author_reveal_zero'] else '非公開'}"
                    )
                if before["submission_close_at"] != after["submission_close_at"]:
                    changed_lines.append(
                        f"投句締切: {format_jst(before['submission_close_at']) if before['submission_close_at'] else '未設定'}"
                        f" → {format_jst(after['submission_close_at']) if after['submission_close_at'] else '未設定'}"
                    )
                if before["selecting_close_at"] != after["selecting_close_at"]:
                    changed_lines.append(
                        f"選句締切: {format_jst(before['selecting_close_at']) if before['selecting_close_at'] else '未設定'}"
                        f" → {format_jst(after['selecting_close_at']) if after['selecting_close_at'] else '未設定'}"
                    )

            extra = "\n締切変更に合わせて通知ジョブを再登録しました。" if deadlines_changed else ""
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


def _sanitize_channel_name(title: str) -> str:
    name = title.replace(" ", "-").replace("　", "-")
    name = re.sub(r'[<>"\'\\|]', "", name)
    return name[:100].strip("-") or "kukai"


def _build_info_embed(kukai, *, select_labels: list | None = None) -> discord.Embed:
    state_ja = STATE_LABEL.get(kukai.state, kukai.state)
    embed = discord.Embed(
        title=f"📋 {kukai.title}",
        description=kukai.description or "",
        color=COLOR_RESULT if kukai.state == "results" else COLOR_INFO,
    )
    if kukai.theme:
        embed.add_field(name="題", value=kukai.theme, inline=True)
    embed.add_field(name="状態", value=state_ja, inline=True)
    if select_labels is not None:
        summary = build_select_summary(kukai.submission_min, kukai.submission_max, select_labels)
        embed.add_field(name="句数", value=summary, inline=False)
    if kukai.submission_close_at:
        embed.add_field(name="投句締切", value=format_jst(kukai.submission_close_at), inline=False)
    if kukai.selecting_close_at:
        embed.add_field(name="選句締切", value=format_jst(kukai.selecting_close_at), inline=False)
    embed.set_footer(text=f"句会ID: {kukai.id}")
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(KukaiCog(bot))
