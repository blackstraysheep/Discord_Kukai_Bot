"""Wizard step 5: Select rule settings (preset selection + per-kukai count/comment)."""

from __future__ import annotations

from typing import Any

import discord

from bot.database import get_session
from bot.services import select_rule_service
from bot.services.errors import ServiceError
from bot.ui.wizard.base import STEP_COUNT, cancel_wizard, goto_step
from bot.ui.wizard.wizard_state import WizardState, set_wizard
from bot.utils.bulk_parser import BulkParseError, parse_label_spec


def _ensure_specs(state: WizardState) -> None:
    if not state.select_label_specs:
        state.select_label_specs = select_rule_service.default_kukai_specs()


def _max_label(spec: dict[str, Any]) -> str:
    return "∞" if spec.get("max_count") is None else str(spec.get("max_count"))


def _specs_as_text(specs: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for spec in specs:
        if spec["label"] == select_rule_service.AUTHOR_COMMENT_LABEL:
            continue
        lines.append(
            ",".join(
                [
                    str(spec["label"]),
                    str(spec.get("point", 0)),
                    str(spec.get("rank_priority") or spec.get("display_order") or 1),
                    str(spec.get("min_count", 0)),
                    _max_label(spec),
                    str(spec.get("comment_mode", "none")),
                ]
            )
        )
    return "\n".join(lines)


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
        if spec["label"] == select_rule_service.AUTHOR_COMMENT_LABEL:
            continue
        lines.append(
            f"**{spec['label']}** {spec['point']:+d}点  "
            f"{spec['min_count']}〜{_max_label(spec)}句  "
            f"コメント:{spec['comment_mode']}"
        )
    if lines:
        embed.add_field(name="選句種別", value="\n".join(lines[:12]), inline=False)
    embed.set_footer(
        text="プリセットを選んだあと「選句数・コメント設定」で句会ごとのカスタマイズができます。"
        " プリセット管理は /preset コマンドで行います。"
    )
    return embed, StepSelectRuleView(state)


class _PresetSelect(discord.ui.Select):
    def __init__(self, state: WizardState) -> None:
        self.state = state
        options = [
            discord.SelectOption(
                label="デフォルト",
                value="default",
                default=state.select_preset_template_id is None
                and state.select_preset_name == "デフォルト",
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
                points_enabled, _ = select_rule_service.deserialize_template_payload(
                    template.definition_json
                )
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


class CountCommentModal(discord.ui.Modal, title="選句数・コメント設定"):
    """ラベルごとに min,max,comment_mode を一括設定するモーダル（最大5ラベル）。"""

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
            max_str = _max_label(row)
            default = f"{row['min_count']},{max_str},{row['comment_mode']}"
            self.add_item(
                discord.ui.TextInput(
                    label=f"{label}（最小,最大,コメント）",
                    placeholder="例: 0,2,optional",
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
                    f"**{label}**: `最小,最大,コメント` の形式で入力してください（例: 0,2,optional）。",
                    ephemeral=True,
                )
                return
            try:
                min_count = int(parts[0])
            except ValueError:
                await interaction.response.send_message(
                    f"**{label}**: 最小は整数で指定してください。", ephemeral=True
                )
                return
            max_part = parts[1].lower()
            if max_part in {"∞", "inf", "infinity", "unlimited", "無制限", ""}:
                max_count = None
            else:
                try:
                    max_count = int(parts[1])
                except ValueError:
                    await interaction.response.send_message(
                        f"**{label}**: 最大は整数または∞で指定してください。", ephemeral=True
                    )
                    return
            comment_mode = parts[2].lower()
            if comment_mode not in {"none", "optional", "required"}:
                await interaction.response.send_message(
                    f"**{label}**: コメントは none/optional/required のいずれかです。",
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


class CustomLabelModal(discord.ui.Modal, title="選句種別の直接入力"):
    labels = discord.ui.TextInput(
        label="1行1件: 名前,点数,rank,最小,最大,コメント",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "特選,2,1,0,1,none\n"
            "並選,1,2,0,5,optional\n"
            "逆選,-1,3,0,1,required"
        ),
        required=True,
        max_length=3000,
    )

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        current = _specs_as_text(state.select_label_specs)
        if current:
            self.labels.default = current

    async def on_submit(self, interaction: discord.Interaction) -> None:
        specs: list[dict[str, object]] = []
        for line_no, raw in enumerate(self.labels.value.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                specs.append(parse_label_spec(line, line_no=line_no))
            except BulkParseError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return

        if not specs:
            await interaction.response.send_message(
                "選句種別を1件以上入力してください。", ephemeral=True
            )
            return

        try:
            normalized = select_rule_service.normalize_kukai_specs(specs, template_id=None)
        except ServiceError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.state.select_preset_template_id = None
        self.state.select_preset_name = "カスタム"
        self.state.select_points_enabled = True
        self.state.select_label_specs = normalized
        set_wizard(self.state)
        embed, view = build(self.state)
        await interaction.response.edit_message(embed=embed, view=view)


class StepSelectRuleView(discord.ui.View):
    def __init__(self, state: WizardState) -> None:
        super().__init__(timeout=900)
        self.state = state
        self.add_item(_PresetSelect(state))

        refresh_btn = discord.ui.Button(
            label="🔄 プリセット再読込",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        refresh_btn.callback = self._refresh_presets
        self.add_item(refresh_btn)

        non_author = [
            r for r in state.select_label_specs
            if r["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
        ]
        count_btn = discord.ui.Button(
            label="📝 選句数・コメント設定",
            style=discord.ButtonStyle.primary,
            row=1,
            disabled=not non_author,
        )
        count_btn.callback = self._edit_counts
        self.add_item(count_btn)

        custom_btn = discord.ui.Button(
            label="選句種別を直接入力",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        custom_btn.callback = self._edit_custom_labels
        self.add_item(custom_btn)

        back_btn = discord.ui.Button(label="← 戻る", style=discord.ButtonStyle.secondary, row=3)
        back_btn.callback = self._back
        self.add_item(back_btn)

        next_btn = discord.ui.Button(label="次へ ➜", style=discord.ButtonStyle.success, row=3)
        next_btn.callback = self._next
        self.add_item(next_btn)

        cancel_btn = discord.ui.Button(label="❌ キャンセル", style=discord.ButtonStyle.danger, row=3)
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

    async def _edit_counts(self, interaction: discord.Interaction) -> None:
        non_author = [
            r for r in self.state.select_label_specs
            if r["label"] != select_rule_service.AUTHOR_COMMENT_LABEL
        ]
        if not non_author:
            await interaction.response.send_message("設定対象のラベルがありません。", ephemeral=True)
            return
        await interaction.response.send_modal(CountCommentModal(self.state))

    async def _edit_custom_labels(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CustomLabelModal(self.state))

    async def _back(self, interaction: discord.Interaction) -> None:
        self.state.step = 4
        await goto_step(interaction, self.state)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.state.step = 6
        await goto_step(interaction, self.state)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await cancel_wizard(interaction, self.state)
