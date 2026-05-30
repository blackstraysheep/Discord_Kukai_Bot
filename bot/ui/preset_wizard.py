"""Preset GUI wizard (2-step): action selection -> configure/confirm."""

from __future__ import annotations

import discord

from bot.database import get_session
from bot.services import preset_service, select_rule_service
from bot.services.errors import ServiceError, ValidationError
from bot.ui.common import ConfirmView
from bot.utils.embed_builder import COLOR_INFO, error_embed, success_embed

_OP_ADD = "add"
_OP_EDIT = "edit"
_OP_DELETE = "delete"
_POINTS_TRUE = {"on", "true", "1", "yes", "y", "有効"}
_POINTS_FALSE = {"off", "false", "0", "no", "n", "無効"}


def _format_labels(labels: list[preset_service.PresetLabelView]) -> str:
    if not labels:
        return "（未設定）"
    lines = []
    for lbl in labels[:12]:
        max_str = "∞" if lbl.max_count is None else str(lbl.max_count)
        lines.append(
            f"- {lbl.label} ({lbl.point:+d}pt) / {lbl.min_count}〜{max_str} / コメント:{lbl.comment_mode}"
        )
    return "\n".join(lines)


def _labels_as_text(labels: list[preset_service.PresetLabelView]) -> str:
    if not labels:
        return ""
    lines: list[str] = []
    for lbl in labels:
        max_str = "∞" if lbl.max_count is None else str(lbl.max_count)
        lines.append(
            f"{lbl.label},{lbl.point},{lbl.min_count},{max_str},{lbl.comment_mode}"
        )
    return "\n".join(lines)


def _parse_points_enabled(raw: str) -> bool:
    value = raw.strip().lower()
    if value in _POINTS_TRUE:
        return True
    if value in _POINTS_FALSE:
        return False
    raise ValidationError("点数機能は on/off（または 有効/無効）で指定してください。")


def _parse_label_rows(raw: str, *, points_enabled: bool) -> list[dict[str, object]]:
    rows = [line.strip() for line in raw.splitlines() if line.strip()]
    if not rows:
        raise ValidationError("ラベルを1件以上入力してください。")

    parsed: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        parts = [p.strip() for p in row.split(",")]
        if not 1 <= len(parts) <= 5:
            raise ValidationError(
                "ラベル行は `ラベル[,点数[,最小[,最大[,コメント]]]]` 形式で入力してください。"
            )

        label = parts[0]
        point_raw = parts[1] if len(parts) >= 2 else ""
        min_raw = parts[2] if len(parts) >= 3 else ""
        max_raw = parts[3] if len(parts) >= 4 else ""
        comment_raw = parts[4].lower() if len(parts) >= 5 else ""

        if not label:
            raise ValidationError(f"ラベル名が空の行があります: `{row}`")
        if label in seen:
            raise ValidationError(f"ラベル「{label}」が重複しています。")
        if label == select_rule_service.AUTHOR_COMMENT_LABEL:
            raise ValidationError("「作者コメント」は予約済みのため設定できません。")

        if points_enabled:
            if point_raw == "":
                raise ValidationError(
                    f"点数機能ONでは `{label},点数` 形式で入力してください。"
                )
            try:
                point = int(point_raw)
            except ValueError as e:
                raise ValidationError(f"ラベル「{label}」の点数は整数で指定してください。") from e
        else:
            point = 0

        if min_raw == "":
            min_count = 0
        else:
            try:
                min_count = int(min_raw)
            except ValueError as e:
                raise ValidationError(f"ラベル「{label}」の最小数は整数で指定してください。") from e

        max_lower = max_raw.lower()
        if max_raw == "" or max_lower in {"∞", "inf", "infinity", "unlimited", "無制限"}:
            max_count = None
        else:
            try:
                max_count = int(max_raw)
            except ValueError as e:
                raise ValidationError(f"ラベル「{label}」の最大数は整数または∞で指定してください。") from e

        if max_count is not None and max_count < min_count:
            raise ValidationError(f"ラベル「{label}」は 最小 <= 最大 で指定してください。")

        comment_mode = comment_raw or "optional"
        if comment_mode not in select_rule_service.COMMENT_MODES:
            raise ValidationError(
                f"ラベル「{label}」のコメント設定は none/optional/required で指定してください。"
            )

        parsed.append(
            {
                "label": label,
                "point": point,
                "min_count": min_count,
                "max_count": max_count,
                "comment_mode": comment_mode,
            }
        )
        seen.add(label)

    return parsed


class _ActionSelect(discord.ui.Select):
    def __init__(self, owner: "PresetWizardView") -> None:
        self._owner = owner
        super().__init__(
            placeholder="操作を選択",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="追加", value=_OP_ADD, default=owner.operation == _OP_ADD),
                discord.SelectOption(label="編集", value=_OP_EDIT, default=owner.operation == _OP_EDIT),
                discord.SelectOption(label="削除", value=_OP_DELETE, default=owner.operation == _OP_DELETE),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self._owner.operation = self.values[0]
        if self._owner.operation == _OP_ADD:
            self._owner.selected_preset_id = None
        self._owner.rebuild()
        await self._owner.respond_edit(interaction)


class _PresetSelect(discord.ui.Select):
    def __init__(self, owner: "PresetWizardView") -> None:
        self._owner = owner
        options = []
        for preset in owner.presets[:25]:
            options.append(
                discord.SelectOption(
                    label=preset.name[:100],
                    value=str(preset.id),
                    description=("既定 / " if preset.is_default else "") +
                    ("点数あり" if preset.points_enabled else "点数なし"),
                    default=preset.id == owner.selected_preset_id,
                )
            )
        if not options:
            options.append(
                discord.SelectOption(
                    label="プリセットなし",
                    value="none",
                    description="先に追加してください",
                    default=True,
                )
            )
        super().__init__(
            placeholder="対象プリセットを選択（編集/削除）",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
            disabled=(owner.operation == _OP_ADD or options[0].value == "none"),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        self._owner.selected_preset_id = None if value == "none" else int(value)
        self._owner.rebuild()
        await self._owner.respond_edit(interaction)


class PresetConfigModal(discord.ui.Modal, title="プリセット設定（ステップ2/2）"):
    def __init__(
        self,
        owner: "PresetWizardView",
        *,
        mode: str,
        preset: preset_service.PresetView | None,
    ) -> None:
        super().__init__()
        self._owner = owner
        self._mode = mode
        self._preset = preset

        default_name = preset.name if preset else ""
        default_points = "on" if (preset.points_enabled if preset else True) else "off"
        default_labels = _labels_as_text(preset.labels if preset else [])

        self._name = discord.ui.TextInput(
            label="プリセット名",
            required=True,
            max_length=100,
            default=default_name,
        )
        self._points = discord.ui.TextInput(
            label="点数機能 (on/off)",
            required=True,
            max_length=10,
            default=default_points,
        )
        self._labels = discord.ui.TextInput(
            label="ラベル一覧（1行: ラベル[,点数[,最小[,最大[,コメント]]]]）",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
            default=default_labels,
            placeholder="特選,2\n並選,1,0,3,optional\n予選,0,,,none",
        )
        self.add_item(self._name)
        self.add_item(self._points)
        self.add_item(self._labels)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            name = self._name.value.strip()
            points_enabled = _parse_points_enabled(self._points.value)
            labels = _parse_label_rows(self._labels.value, points_enabled=points_enabled)

            async with get_session() as session:
                if self._mode == _OP_ADD:
                    created = await preset_service.create_preset(
                        session,
                        guild_id=interaction.guild.id,
                        created_by=interaction.user.id,
                        name=name,
                        points_enabled=points_enabled,
                        set_default=False,
                    )
                    updated = await preset_service.replace_labels(
                        session,
                        guild_id=interaction.guild.id,
                        preset_id=created.id,
                        labels=labels,
                    )
                    target = updated
                else:
                    assert self._preset is not None
                    if name != self._preset.name:
                        await preset_service.rename_preset(
                            session, interaction.guild.id, self._preset.id, name
                        )
                    if points_enabled != self._preset.points_enabled:
                        await preset_service.set_preset_points(
                            session, interaction.guild.id, self._preset.id, points_enabled
                        )
                    updated = await preset_service.replace_labels(
                        session,
                        guild_id=interaction.guild.id,
                        preset_id=self._preset.id,
                        labels=labels,
                    )
                    target = updated

            await self._owner.reload()
            await interaction.followup.send(
                embed=success_embed(
                    f"プリセット「**{target.name}**」を保存しました。\n"
                    f"点数: {'有効' if target.points_enabled else '無効'} / ラベル数: {len(target.labels)}"
                ),
                ephemeral=True,
            )
            await self._owner.safe_edit_message()
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


class PresetWizardView(discord.ui.View):
    def __init__(self, *, guild_id: int, user_id: int, presets: list[preset_service.PresetView]) -> None:
        super().__init__(timeout=1800)
        self.guild_id = guild_id
        self.user_id = user_id
        self.presets = presets
        self.operation = _OP_ADD
        self.selected_preset_id: int | None = presets[0].id if presets else None
        self.message: discord.InteractionMessage | None = None
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=error_embed("このウィザードは実行者のみ操作できます。"),
                ephemeral=True,
            )
            return False
        return True

    def current_preset(self) -> preset_service.PresetView | None:
        if self.selected_preset_id is None:
            return None
        return next((p for p in self.presets if p.id == self.selected_preset_id), None)

    def build_embed(self) -> discord.Embed:
        current = self.current_preset()
        op_label = {"add": "追加", "edit": "編集", "delete": "削除"}.get(self.operation, self.operation)
        embed = discord.Embed(
            title="選句プリセットGUIウィザード",
            description=(
                "ステップ1/2: 操作と対象を選択\n"
                "ステップ2/2: 設定入力（追加/編集）または削除確認"
            ),
            color=COLOR_INFO,
        )
        embed.add_field(name="操作", value=op_label, inline=True)
        if current:
            embed.add_field(name="対象", value=f"[{current.id}] {current.name}", inline=True)
            embed.add_field(
                name="設定",
                value=f"点数: {'有効' if current.points_enabled else '無効'}\nラベル: {len(current.labels)}件",
                inline=False,
            )
            embed.add_field(name="ラベル一覧", value=_format_labels(current.labels), inline=False)
        else:
            target_text = "新規作成（対象選択不要）" if self.operation == _OP_ADD else "未選択"
            embed.add_field(name="対象", value=target_text, inline=True)
        return embed

    def rebuild(self) -> None:
        self.clear_items()
        self.add_item(_ActionSelect(self))
        self.add_item(_PresetSelect(self))

        close_btn = discord.ui.Button(
            label="閉じる",
            style=discord.ButtonStyle.danger,
            row=2,
        )
        close_btn.callback = self._on_close
        self.add_item(close_btn)

        refresh_btn = discord.ui.Button(
            label="再読込",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        refresh_btn.callback = self._on_refresh
        self.add_item(refresh_btn)

        next_btn = discord.ui.Button(
            label="次へ（ステップ2）",
            style=discord.ButtonStyle.success,
            row=2,
        )
        next_btn.callback = self._on_next
        self.add_item(next_btn)

    async def reload(self) -> None:
        async with get_session() as session:
            self.presets = await preset_service.list_presets(session, self.guild_id)
        if self.selected_preset_id is not None:
            if not any(p.id == self.selected_preset_id for p in self.presets):
                self.selected_preset_id = self.presets[0].id if self.presets else None
        self.rebuild()

    async def safe_edit_message(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(embed=self.build_embed(), view=self)
        except Exception:
            return

    async def _send_expired_notice(self, interaction: discord.Interaction) -> None:
        msg = "このプリセットGUIの操作期限が切れました。`/preset gui` を開き直してください。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed(msg), ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed(msg), ephemeral=True)
        except Exception:
            return

    async def respond_edit(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=self.build_embed(), view=self)
            else:
                await interaction.response.edit_message(embed=self.build_embed(), view=self)
        except discord.NotFound:
            self.stop()
            await self._send_expired_notice(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        current = self.current_preset()
        if self.operation in {_OP_EDIT, _OP_DELETE} and current is None:
            await interaction.response.send_message(
                embed=error_embed("編集/削除するプリセットを選択してください。"),
                ephemeral=True,
            )
            return

        if self.operation == _OP_DELETE:
            assert current is not None
            view = ConfirmView()
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⚠️ プリセット削除（ステップ2/2）",
                    description=f"プリセット「**{current.name}**」を削除します。よろしいですか？",
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
            try:
                async with get_session() as session:
                    deleted = await preset_service.delete_preset(session, self.guild_id, current.id)
                await self.reload()
                await interaction.edit_original_response(
                    embed=success_embed(f"プリセット「**{deleted.name}**」を削除しました。"),
                    view=None,
                )
                await self.safe_edit_message()
            except ServiceError as e:
                await interaction.edit_original_response(embed=error_embed(str(e)), view=None)
            return

        await interaction.response.send_modal(
            PresetConfigModal(
                self,
                mode=self.operation,
                preset=current if self.operation == _OP_EDIT else None,
            )
        )

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        await self.reload()
        await self.respond_edit(interaction)

    async def _on_close(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.edit_message(
                embed=discord.Embed(description="プリセットGUIを閉じました。", color=COLOR_INFO),
                view=None,
            )
        except discord.NotFound:
            self.stop()


async def open_preset_wizard(interaction: discord.Interaction) -> None:
    assert interaction.guild is not None
    async with get_session() as session:
        presets = await preset_service.list_presets(session, interaction.guild.id)
    view = PresetWizardView(
        guild_id=interaction.guild.id,
        user_id=interaction.user.id,
        presets=presets,
    )
    await interaction.response.send_message(
        embed=view.build_embed(),
        view=view,
        ephemeral=True,
    )
    view.message = await interaction.original_response()
