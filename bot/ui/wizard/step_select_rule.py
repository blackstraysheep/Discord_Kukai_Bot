"""Wizard step 5: Select rule settings (preset + per-kukai customization)."""

from __future__ import annotations

from typing import Any

import discord

from bot.database import get_session
from bot.services import select_rule_service
from bot.services.errors import ServiceError
from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard


def _ensure_specs(state: WizardState) -> None:
    if not state.select_label_specs:
        state.select_label_specs = select_rule_service.default_kukai_specs()
    if not state.selected_select_label:
        non_author = [
            spec["label"]
            for spec in state.select_label_specs
            if spec["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
        ]
        state.selected_select_label = non_author[0] if non_author else ""


def _max_label(spec: dict[str, Any]) -> str:
    return "∞" if spec.get("max_count") is None else str(spec.get("max_count"))


def build(state: WizardState) -> tuple[discord.Embed, discord.ui.View]:
    _ensure_specs(state)

    embed = discord.Embed(
        title=f"ステップ 5/{STEP_COUNT}: 選句設定",
        color=discord.Color.blurple(),
    )
    points_label = "有効" if state.select_points_enabled else "無効（全ラベル0点）"
    embed.add_field(
        name="選択中プリセット",
        value=f"{state.select_preset_name}\n点数: {points_label}",
        inline=False,
    )

    lines: list[str] = []
    for spec in state.select_label_specs:
        marker = "👉 " if spec["label"] == state.selected_select_label else ""
        lines.append(
            f"{marker}{spec['label']} / {spec['point']:+d}pt / "
            f"{spec['min_count']}〜{_max_label(spec)} / comment:{spec['comment_mode']}"
        )
    embed.add_field(name="選句種別", value="\n".join(lines[:12]), inline=False)
    embed.set_footer(text="プリセット選択（または新規作成）後、ラベルごとに選句数/コメント設定を行います。")
    return embed, StepSelectRuleView(state)


class _PresetSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        options = [
            discord.SelectOption(
                label="デフォルト",
                value="default",
                default=state.select_preset_template_id is None,
            )
        ]
        for row in state.select_preset_options[:24]:
            template_id = int(row["id"])
            name = str(row["name"])
            options.append(
                discord.SelectOption(
                    label=name,
                    value=str(template_id),
                    default=state.select_preset_template_id == template_id,
                )
            )
        super().__init__(
            placeholder="プリセットを選択",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        if selected == "default":
            self.state.select_preset_template_id = None
            self.state.select_preset_name = "デフォルト"
            self.state.select_points_enabled = True
            self.state.select_label_specs = select_rule_service.default_kukai_specs()
            _ensure_specs(self.state)
            set_wizard(self.state)
            embed, view = build(self.state)
            await interaction.response.edit_message(embed=embed, view=view)
            return

        template_id = int(selected)
        try:
            async with get_session() as session:
                template = await select_rule_service.get_template(
                    session, self.state.guild_id, template_id
                )
                points_enabled, _ = select_rule_service.deserialize_template_payload(template.definition_json)
                specs = select_rule_service.build_kukai_specs_from_template(template)
        except ServiceError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.state.select_preset_template_id = template.id
        self.state.select_preset_name = template.name
        self.state.select_points_enabled = points_enabled
        self.state.select_label_specs = specs
        _ensure_specs(self.state)
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class _LabelSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        options = []
        for spec in state.select_label_specs:
            if spec["label"] == select_rule_service.AUTHOR_COMMENT_LABEL:
                continue
            options.append(
                discord.SelectOption(
                    label=spec["label"],
                    value=spec["label"],
                    description=f"{spec['point']:+d}pt / {spec['min_count']}〜{_max_label(spec)}",
                    default=spec["label"] == state.selected_select_label,
                )
            )
        if not options:
            options = [
                discord.SelectOption(
                    label="選択不可",
                    value="disabled",
                    default=True,
                )
            ]
        super().__init__(
            placeholder="選句種別を選択",
            min_values=1,
            max_values=1,
            options=options,
            disabled=(options[0].value == "disabled"),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        if value != "disabled":
            self.state.selected_select_label = value
            set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class CountEditModal(discord.ui.Modal, title="選句数のカスタマイズ"):
    min_count = discord.ui.TextInput(label="最小選句数", placeholder="0", required=True, max_length=3)
    max_count = discord.ui.TextInput(
        label="最大選句数（∞可）",
        placeholder="1 / ∞",
        required=False,
        max_length=8,
    )

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        spec = next(
            (row for row in state.select_label_specs if row["label"] == state.selected_select_label),
            None,
        )
        if spec:
            self.min_count.default = str(spec.get("min_count", 0))
            self.max_count.default = _max_label(spec)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            min_count = int(self.min_count.value)
        except ValueError:
            await interaction.response.send_message("最小選句数は整数で指定してください。", ephemeral=True)
            return

        raw_max = self.max_count.value.strip()
        if not raw_max or raw_max.lower() in {"∞", "inf", "infinity", "unlimited", "無制限"}:
            max_count = None
        else:
            try:
                max_count = int(raw_max)
            except ValueError:
                await interaction.response.send_message("最大選句数は整数または∞で指定してください。", ephemeral=True)
                return

        changed = False
        for spec in self.state.select_label_specs:
            if spec["label"] == self.state.selected_select_label:
                spec["min_count"] = min_count
                spec["max_count"] = max_count
                changed = True
                break
        if not changed:
            await interaction.response.send_message("対象の選句種別が見つかりません。", ephemeral=True)
            return

        try:
            self.state.select_label_specs = select_rule_service.normalize_kukai_specs(
                self.state.select_label_specs,
                template_id=self.state.select_preset_template_id,
            )
        except ServiceError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class AddSelectTypeModal(discord.ui.Modal, title="選句種別を追加"):
    label = discord.ui.TextInput(label="種別名", placeholder="敢闘賞", required=True, max_length=50)
    point = discord.ui.TextInput(label="点数", placeholder="0", required=True, max_length=5)
    min_count = discord.ui.TextInput(label="最小選句数", placeholder="0", required=True, max_length=3)
    max_count = discord.ui.TextInput(
        label="最大選句数（∞可）",
        placeholder="1 / ∞",
        required=False,
        max_length=8,
    )
    comment_mode = discord.ui.TextInput(
        label="comment_mode",
        placeholder="none / optional / required",
        required=False,
        max_length=10,
        default="none",
    )

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        label = self.label.value.strip()
        if not label:
            await interaction.response.send_message("種別名は必須です。", ephemeral=True)
            return
        if label == select_rule_service.AUTHOR_COMMENT_LABEL:
            await interaction.response.send_message("「作者コメント」は追加できません。", ephemeral=True)
            return

        try:
            point = int(self.point.value.strip())
            min_count = int(self.min_count.value.strip())
        except ValueError:
            await interaction.response.send_message("点数/最小選句数は整数で指定してください。", ephemeral=True)
            return
        if not self.state.select_points_enabled:
            point = 0

        raw_max = self.max_count.value.strip()
        if not raw_max or raw_max.lower() in {"∞", "inf", "infinity", "unlimited", "無制限"}:
            max_count = None
        else:
            try:
                max_count = int(raw_max)
            except ValueError:
                await interaction.response.send_message("最大選句数は整数または∞で指定してください。", ephemeral=True)
                return

        comment_mode = (self.comment_mode.value or "none").strip().lower()

        if any(row["label"] == label for row in self.state.select_label_specs):
            await interaction.response.send_message("同名の選句種別が既に存在します。", ephemeral=True)
            return

        non_author = [
            row for row in self.state.select_label_specs
            if row["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
        ]
        author = next(
            (row for row in self.state.select_label_specs if row["label"] == select_rule_service.AUTHOR_COMMENT_LABEL),
            None,
        )
        non_author.append(
            {
                "label": label,
                "point": point,
                "min_count": min_count,
                "max_count": max_count,
                "comment_mode": comment_mode,
            }
        )
        merged = non_author + ([author] if author else [])
        try:
            self.state.select_label_specs = select_rule_service.normalize_kukai_specs(
                merged,
                template_id=self.state.select_preset_template_id,
            )
        except ServiceError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.state.selected_select_label = label
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class CreatePresetModal(discord.ui.Modal, title="新規プリセット作成"):
    preset_name = discord.ui.TextInput(
        label="プリセット名",
        placeholder="五句取り（無点）",
        required=True,
        max_length=100,
    )
    points_enabled = discord.ui.TextInput(
        label="点数機能",
        placeholder="on / off",
        required=False,
        default="on",
        max_length=8,
    )

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        points_raw = (self.points_enabled.value or "on").strip().lower()
        points_enabled = points_raw not in {"off", "false", "0", "無効"}
        try:
            async with get_session() as session:
                template = await select_rule_service.create_or_update_template(
                    session,
                    guild_id=self.state.guild_id,
                    created_by=self.state.user_id,
                    name=self.preset_name.value.strip(),
                    points_enabled=points_enabled,
                    set_default=False,
                )
                templates = await select_rule_service.list_templates(session, self.state.guild_id)
        except ServiceError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.state.select_preset_options = [{"id": t.id, "name": t.name} for t in templates]
        self.state.select_preset_template_id = template.id
        self.state.select_preset_name = template.name
        self.state.select_points_enabled = points_enabled
        self.state.select_label_specs = select_rule_service.default_kukai_specs()
        if not points_enabled:
            for spec in self.state.select_label_specs:
                spec["point"] = 0
        _ensure_specs(self.state)
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class BulkRuleModal(discord.ui.Modal, title="ラベル一括設定"):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        self._labels: list[str] = []
        non_author = [
            row for row in state.select_label_specs
            if row["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
        ]
        for row in non_author[:5]:
            label = row["label"]
            self._labels.append(label)
            default = f"{row['min_count']},{_max_label(row)},{row['comment_mode']}"
            self.add_item(
                discord.ui.TextInput(
                    label=label,
                    placeholder="min,max,comment_mode 例: 0,2,optional",
                    required=True,
                    default=default,
                    max_length=30,
                )
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        updates: dict[str, tuple[int, int | None, str]] = {}
        for label, item in zip(self._labels, self.children):
            assert isinstance(item, discord.ui.TextInput)
            raw = item.value.strip()
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) != 3:
                await interaction.response.send_message(
                    f"{label}: `min,max,comment_mode` 形式で入力してください。",
                    ephemeral=True,
                )
                return
            try:
                min_count = int(parts[0])
            except ValueError:
                await interaction.response.send_message(f"{label}: min は整数です。", ephemeral=True)
                return
            max_part = parts[1].lower()
            if max_part in {"∞", "inf", "infinity", "unlimited", "無制限", ""}:
                max_count = None
            else:
                try:
                    max_count = int(parts[1])
                except ValueError:
                    await interaction.response.send_message(f"{label}: max は整数または∞です。", ephemeral=True)
                    return
            comment_mode = parts[2].lower()
            if comment_mode not in {"none", "optional", "required"}:
                await interaction.response.send_message(
                    f"{label}: comment_mode は none/optional/required です。",
                    ephemeral=True,
                )
                return
            updates[label] = (min_count, max_count, comment_mode)

        for row in self.state.select_label_specs:
            if row["label"] in updates:
                min_count, max_count, comment_mode = updates[row["label"]]
                row["min_count"] = min_count
                row["max_count"] = max_count
                row["comment_mode"] = comment_mode

        try:
            self.state.select_label_specs = select_rule_service.normalize_kukai_specs(
                self.state.select_label_specs,
                template_id=self.state.select_preset_template_id,
            )
        except ServiceError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class StepSelectRuleView(discord.ui.View):
    def __init__(self, state: WizardState) -> None:
        super().__init__(timeout=900)
        self.state = state
        self.add_item(_PresetSelect(state))
        self.add_item(_LabelSelect(state))

        refresh_btn = discord.ui.Button(
            label="🔄 プリセット再読込",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        refresh_btn.callback = self._refresh_presets
        self.add_item(refresh_btn)

        create_preset_btn = discord.ui.Button(
            label="🆕 新規プリセット作成",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        create_preset_btn.callback = self._create_preset
        self.add_item(create_preset_btn)

        edit_bulk_btn = discord.ui.Button(
            label="🧩 ラベル一括設定",
            style=discord.ButtonStyle.primary,
            row=3,
        )
        edit_bulk_btn.callback = self._edit_bulk
        self.add_item(edit_bulk_btn)

        add_label_btn = discord.ui.Button(
            label="➕ 種別追加",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        add_label_btn.callback = self._add_label
        self.add_item(add_label_btn)

        remove_label_btn = discord.ui.Button(
            label="🗑️ 種別削除",
            style=discord.ButtonStyle.danger,
            row=4,
        )
        remove_label_btn.callback = self._remove_label
        self.add_item(remove_label_btn)

        back_btn = discord.ui.Button(label="← 戻る", style=discord.ButtonStyle.secondary, row=5)
        back_btn.callback = self._back
        self.add_item(back_btn)

        next_btn = discord.ui.Button(label="次へ ➜", style=discord.ButtonStyle.success, row=5)
        next_btn.callback = self._next
        self.add_item(next_btn)

        cancel_btn = discord.ui.Button(label="❌ キャンセル", style=discord.ButtonStyle.danger, row=5)
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _refresh_presets(self, interaction: discord.Interaction) -> None:
        try:
            async with get_session() as session:
                templates = await select_rule_service.list_templates(session, self.state.guild_id)
        except ServiceError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        self.state.select_preset_options = [{"id": t.id, "name": t.name} for t in templates]
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _create_preset(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CreatePresetModal(self.state))

    async def _edit_bulk(self, interaction: discord.Interaction) -> None:
        non_author = [
            row for row in self.state.select_label_specs
            if row["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
        ]
        if not non_author:
            await interaction.response.send_message("設定対象のラベルがありません。", ephemeral=True)
            return
        await interaction.response.send_modal(BulkRuleModal(self.state))

    async def _add_label(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddSelectTypeModal(self.state))

    async def _remove_label(self, interaction: discord.Interaction) -> None:
        label = self.state.selected_select_label
        if not label:
            await interaction.response.send_message("削除対象の選句種別を選択してください。", ephemeral=True)
            return
        if label == select_rule_service.AUTHOR_COMMENT_LABEL:
            await interaction.response.send_message("作者コメントは削除できません。", ephemeral=True)
            return

        remaining_non_author = [
            row for row in self.state.select_label_specs
            if row["label"] != label and row["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
        ]
        if not remaining_non_author:
            await interaction.response.send_message("選句種別は1件以上必要です。", ephemeral=True)
            return

        self.state.select_label_specs = [
            row for row in self.state.select_label_specs if row["label"] != label
        ]
        self.state.select_label_specs = select_rule_service.normalize_kukai_specs(
            self.state.select_label_specs,
            template_id=self.state.select_preset_template_id,
        )
        self.state.selected_select_label = remaining_non_author[0]["label"]
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 4
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 6
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)
