"""Selecting UI wizard: choose submission, choose label, then comment."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from sqlalchemy import select

from bot.database import get_session
from bot.models.select_rule import SelectLabel
from bot.repositories import submission_repo, select_repo
from bot.services import kukai_service, select_service
from bot.services.errors import ServiceError
from bot.utils.embed_builder import COLOR_INFO, COLOR_SUCCESS, error_embed
from bot.utils.text import discord_safe

if TYPE_CHECKING:
    from bot.models.submission import PublishedSubmission
    from bot.models.select import Select

_AUTHOR_COMMENT_LABEL = "作者コメント"
_OVERALL_VALUE = "__overall__"
SELECT_COMMENT_PREVIEW_LIMIT = 120
OVERALL_COMMENT_PREVIEW_LIMIT = 300
logger = logging.getLogger(__name__)


async def load_select_data(
    session, kukai_id: int, selector_user_id: int
) -> tuple[list[PublishedSubmission], list[SelectLabel], dict[int, Select], str]:
    pub_subs = await submission_repo.list_published(session, kukai_id)
    result = await session.execute(
        select(SelectLabel)
        .where(SelectLabel.kukai_id == kukai_id)
        .order_by(SelectLabel.display_order)
    )
    labels = list(result.scalars().all())
    selects = await select_repo.get_selects_by_selector(session, kukai_id, selector_user_id)
    selects_by_sub = {sel.submission_id: sel for sel in selects}
    overall = await select_repo.get_overall_comment(session, kukai_id, selector_user_id)
    return pub_subs, labels, selects_by_sub, (overall.comment if overall else "")


def _select_snapshot(pub_subs: list[PublishedSubmission], selects_by_sub: dict[int, Select], overall_comment: str) -> str:
    rows: list[str] = []
    for item in sorted(pub_subs, key=lambda x: x.number):
        selected = selects_by_sub.get(item.submission_id)
        if not selected:
            continue
        label_name = "作者コメント" if selected.is_self_comment else (
            selected.select_label.label if selected.select_label else "?"
        )
        comment_part = ""
        if selected.comment:
            comment_part = f" — {discord_safe(selected.comment.comment[:SELECT_COMMENT_PREVIEW_LIMIT])}"
        rows.append(f"No.{item.number} **{label_name}**{comment_part}")
    if overall_comment:
        rows.append(f"総評 — {discord_safe(overall_comment[:OVERALL_COMMENT_PREVIEW_LIMIT])}")
    if not rows:
        return "（未登録）"
    if len(rows) > 10:
        return "\n".join(rows[:10] + [f"...他 {len(rows) - 10} 件"])
    return "\n".join(rows)


class SelectCommentModal(discord.ui.Modal, title="コメント入力"):
    def __init__(
        self,
        kukai_id: int,
        submission_id: int,
        select_label_id: int,
        selector_user_id: int,
        selected_submission_id: int,
        *,
        required: bool,
        is_self_comment: bool = False,
        current_text: str = "",
    ) -> None:
        super().__init__()
        self.kukai_id = kukai_id
        self.submission_id = submission_id
        self.select_label_id = select_label_id
        self.selector_user_id = selector_user_id
        self.selected_submission_id = selected_submission_id
        self.is_self_comment = is_self_comment
        self._comment = discord.ui.TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=required,
            default=current_text,
        )
        self.add_item(self._comment)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                await select_service.cast_select(
                    session,
                    kukai,
                    self.selector_user_id,
                    self.submission_id,
                    self.select_label_id,
                    comment=self._comment.value or None,
                    is_self_comment=self.is_self_comment,
                )
                pub_subs, labels, selects_by_sub, overall_comment = await load_select_data(
                    session, kukai.id, self.selector_user_id
                )
            view = SelectView(
                kukai,
                pub_subs,
                labels,
                selects_by_sub,
                overall_comment=overall_comment,
                selector_user_id=self.selector_user_id,
                selected_submission_id=self.selected_submission_id,
            )
            await interaction.edit_original_response(embed=view.build_embed(), view=view)
            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ 選評を登録しました",
                    description=_select_snapshot(pub_subs, selects_by_sub, overall_comment),
                    color=COLOR_SUCCESS,
                ),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
        except Exception:
            logger.exception("SelectCommentModal.on_submit failed")
            await interaction.followup.send(
                embed=error_embed("選句保存中に内部エラーが発生しました。ログを確認してください。"),
                ephemeral=True,
            )


class OverallSelectCommentModal(discord.ui.Modal, title="総評を入力"):
    def __init__(self, kukai_id: int, selector_user_id: int, current_text: str = "") -> None:
        super().__init__()
        self.kukai_id = kukai_id
        self.selector_user_id = selector_user_id
        self._comment = discord.ui.TextInput(
            label="総評",
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
            default=current_text,
        )
        self.add_item(self._comment)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                await select_service.set_overall_comment(
                    session, kukai, self.selector_user_id, self._comment.value
                )
            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ 総評を保存しました",
                    description=discord_safe(self._comment.value[:OVERALL_COMMENT_PREVIEW_LIMIT]),
                    color=COLOR_SUCCESS,
                ),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
        except Exception:
            logger.exception("OverallSelectCommentModal.on_submit failed")
            await interaction.followup.send(
                embed=error_embed("総評保存中に内部エラーが発生しました。ログを確認してください。"),
                ephemeral=True,
            )


class _SubmissionSelect(discord.ui.Select):
    def __init__(self, view_owner: "SelectView") -> None:
        self._view_owner = view_owner
        options = []
        for ps in view_owner._pub_subs[:25]:
            options.append(
                discord.SelectOption(
                    label=f"No.{ps.number}",
                    value=str(ps.submission_id),
                    description=(ps.submission.text[:95] or "（空）"),
                    default=(not view_owner._is_overall_selected() and ps.submission_id == view_owner._selected_submission_id),
                )
            )
        options.append(
            discord.SelectOption(
                label="総評",
                value=_OVERALL_VALUE,
                description="句会全体への総評を入力",
                default=view_owner._is_overall_selected(),
            )
        )
        super().__init__(
            placeholder="番号+句リストから1句選択",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        self._view_owner._selected_submission_id = None if selected == _OVERALL_VALUE else int(selected)
        self._view_owner._selected_label_value = self._view_owner._default_label_value()
        self._view_owner._build_items()
        await interaction.response.edit_message(
            embed=self._view_owner.build_embed(),
            view=self._view_owner,
        )


class _LabelSelect(discord.ui.Select):
    def __init__(self, view_owner: "SelectView") -> None:
        self._view_owner = view_owner
        if view_owner._is_overall_selected():
            options = [
                discord.SelectOption(
                    label="総評",
                    value="overall_comment",
                    description="句会全体へのコメント",
                    default=True,
                )
            ]
            super().__init__(
                placeholder="選種別を選択",
                options=options,
                min_values=1,
                max_values=1,
                row=1,
            )
            return

        ps = view_owner._selected_ps()
        is_own = ps.submission.user_id == view_owner._selector_user_id

        options: list[discord.SelectOption] = []
        if is_own:
            if view_owner._author_label is not None:
                options.append(
                    discord.SelectOption(
                        label=_AUTHOR_COMMENT_LABEL,
                        value="author_comment",
                        description="自分の句には作者コメントのみ設定可能",
                        default=view_owner._selected_label_value == "author_comment",
                    )
                )
        else:
            for lbl in view_owner._labels:
                if lbl.label == _AUTHOR_COMMENT_LABEL:
                    continue
                desc = f"{lbl.point:+d}点"
                if lbl.max_count is not None:
                    desc += f" | 最大{lbl.max_count}票"
                options.append(
                    discord.SelectOption(
                        label=lbl.label,
                        value=str(lbl.id),
                        description=desc,
                        default=view_owner._selected_label_value == str(lbl.id),
                    )
                )

        if not options:
            options = [
                discord.SelectOption(
                    label="選択不可",
                    value="disabled",
                    description="利用可能な選種別がありません",
                    default=True,
                )
            ]

        super().__init__(
            placeholder="選種別を選択",
            options=options[:25],
            min_values=1,
            max_values=1,
            row=1,
            disabled=(options[0].value == "disabled"),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self._view_owner._selected_label_value = self.values[0]
        self._view_owner._build_items()
        await interaction.response.edit_message(
            embed=self._view_owner.build_embed(),
            view=self._view_owner,
        )


class SelectView(discord.ui.View):
    def __init__(
        self,
        kukai,
        pub_subs: list[PublishedSubmission],
        labels: list[SelectLabel],
        selects_by_sub: dict[int, Select],
        overall_comment: str,
        *,
        selector_user_id: int,
        selected_submission_id: int | None = None,
    ) -> None:
        super().__init__(timeout=600)
        self._kukai = kukai
        self._pub_subs = sorted(pub_subs, key=lambda x: x.number)
        self._labels = labels
        self._selects = selects_by_sub
        self._overall_comment = overall_comment
        self._selector_user_id = selector_user_id
        self._author_label = next((lbl for lbl in labels if lbl.label == _AUTHOR_COMMENT_LABEL), None)
        self._selected_submission_id = (
            selected_submission_id if selected_submission_id is not None else self._pub_subs[0].submission_id
        )
        self._selected_label_value = self._default_label_value()
        self._build_items()

    def _is_overall_selected(self) -> bool:
        return self._selected_submission_id is None

    def _selected_ps(self) -> PublishedSubmission:
        for ps in self._pub_subs:
            if ps.submission_id == self._selected_submission_id:
                return ps
        return self._pub_subs[0]

    def _default_label_value(self) -> str:
        if self._is_overall_selected():
            return "overall_comment"
        ps = self._selected_ps()
        current_select = self._selects.get(ps.submission_id)
        if ps.submission.user_id == self._selector_user_id:
            return "author_comment"
        if current_select is not None:
            return str(current_select.select_label_id)
        for lbl in self._labels:
            if lbl.label != _AUTHOR_COMMENT_LABEL:
                return str(lbl.id)
        return "disabled"

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"選句ウィザード — {self._kukai.title}",
            color=COLOR_INFO,
        )
        embed.description = (
            "1) 句を選択 → 2) 選種別を選択 → 3) 必要ならコメント入力\n"
            "（自分の句は作者コメントのみ設定できます）"
        )
        lines = [f"`No.{item.number}` {discord_safe(item.submission.text[:70])}" for item in self._pub_subs[:25]]
        if len(self._pub_subs) > 25:
            lines.append(f"...他 {len(self._pub_subs) - 25} 句")
        lines.append("`総評` 句会全体へのコメント")
        embed.add_field(name="番号+句リスト", value="\n".join(lines), inline=False)
        embed.add_field(name="選句数", value=self._select_count_summary(), inline=False)

        if self._is_overall_selected():
            embed.add_field(name="選択中 総評", value=self._overall_comment[:500] or "（未入力）", inline=False)
            embed.add_field(name="現在の状態", value="現在: **総評**", inline=False)
            embed.set_footer(text=f"句会 ID: {self._kukai.id}")
            return embed

        ps = self._selected_ps()
        current_select = self._selects.get(ps.submission_id)
        is_own = ps.submission.user_id == self._selector_user_id
        embed.add_field(name=f"選択中 No.{ps.number}", value=f"```{ps.submission.text}```", inline=False)

        if current_select:
            label_name = current_select.select_label.label if current_select.select_label else "?"
            if current_select.is_self_comment:
                label_name = "作者コメント"
            status = f"現在: **{label_name}**"
            if current_select.comment:
                status += f"\n> {discord_safe(current_select.comment.comment[:SELECT_COMMENT_PREVIEW_LIMIT])}"
        else:
            status = "現在: （未選択）"
        if is_own:
            status += "\n自句: 作者コメントのみ可"
        embed.add_field(name="現在の状態", value=status, inline=False)
        embed.set_footer(text=f"句会 ID: {self._kukai.id}")
        return embed

    def _select_count_summary(self) -> str:
        counts: dict[int, int] = {}
        for selected in self._selects.values():
            if selected.is_self_comment:
                continue
            counts[selected.select_label_id] = counts.get(selected.select_label_id, 0) + 1

        rows: list[str] = []
        for lbl in self._labels:
            if lbl.label == _AUTHOR_COMMENT_LABEL:
                continue
            current = counts.get(lbl.id, 0)
            max_label = "∞" if lbl.max_count is None else str(lbl.max_count)
            if lbl.min_count > 0:
                target = f"{lbl.min_count}〜{max_label}"
            else:
                target = max_label
            rows.append(f"{lbl.label}: **{current}** / {target}")
        return "\n".join(rows) if rows else "（通常選句ラベルなし）"

    def _build_items(self) -> None:
        self.clear_items()
        self.add_item(_SubmissionSelect(self))
        self.add_item(_LabelSelect(self))

        decide_btn = discord.ui.Button(label="✅ 決定", style=discord.ButtonStyle.success, row=2)
        decide_btn.callback = self._on_decide
        self.add_item(decide_btn)

        remove_btn = discord.ui.Button(label="🗑️ 取消", style=discord.ButtonStyle.danger, row=2)
        remove_btn.callback = self._on_remove
        self.add_item(remove_btn)

        done_btn = discord.ui.Button(label="完了", style=discord.ButtonStyle.secondary, row=2)
        done_btn.callback = self._on_done
        self.add_item(done_btn)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_session() as session:
            kukai = await kukai_service.get_kukai(session, self._kukai.id, interaction.guild.id)
            pub_subs, labels, selects_by_sub, overall_comment = await load_select_data(
                session, self._kukai.id, self._selector_user_id
            )
        view = SelectView(
            kukai,
            pub_subs,
            labels,
            selects_by_sub,
            overall_comment=overall_comment,
            selector_user_id=self._selector_user_id,
            selected_submission_id=self._selected_submission_id,
        )
        await interaction.edit_original_response(embed=view.build_embed(), view=view)

    async def _on_decide(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if self._is_overall_selected():
            await self._on_overall(interaction)
            return
        ps = self._selected_ps()
        current_select = self._selects.get(ps.submission_id)
        current_text = current_select.comment.comment if current_select and current_select.comment else ""
        is_own = ps.submission.user_id == self._selector_user_id

        if is_own:
            if self._author_label is None:
                await interaction.response.send_message(
                    embed=error_embed("「作者コメント」ラベルが未設定です。管理者に連絡してください。"),
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(
                SelectCommentModal(
                    self._kukai.id,
                    ps.submission_id,
                    self._author_label.id,
                    self._selector_user_id,
                    selected_submission_id=ps.submission_id,
                    required=True,
                    is_self_comment=True,
                    current_text=current_text,
                )
            )
            return

        if self._selected_label_value in {"", "disabled", "author_comment"}:
            await interaction.response.send_message(
                embed=error_embed("選種別を選択してください。"),
                ephemeral=True,
            )
            return
        label = next((lbl for lbl in self._labels if str(lbl.id) == self._selected_label_value), None)
        if label is None:
            await interaction.response.send_message(
                embed=error_embed("選種別が見つかりません。"),
                ephemeral=True,
            )
            return

        if label.comment_mode in {"optional", "required"}:
            await interaction.response.send_modal(
                SelectCommentModal(
                    self._kukai.id,
                    ps.submission_id,
                    label.id,
                    self._selector_user_id,
                    selected_submission_id=ps.submission_id,
                    required=(label.comment_mode == "required"),
                    is_self_comment=False,
                    current_text=current_text,
                )
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self._kukai.id, interaction.guild.id)
                await select_service.cast_select(
                    session,
                    kukai,
                    self._selector_user_id,
                    ps.submission_id,
                    label.id,
                )
                pub_subs, labels, selects_by_sub, overall_comment = await load_select_data(
                    session, self._kukai.id, self._selector_user_id
                )
            await self._refresh(interaction)
            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ 選評を登録しました",
                    description=_select_snapshot(pub_subs, selects_by_sub, overall_comment),
                    color=COLOR_SUCCESS,
                ),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
        except Exception:
            logger.exception("SelectView._on_decide failed")
            await interaction.followup.send(
                embed=error_embed("選句保存中に内部エラーが発生しました。ログを確認してください。"),
                ephemeral=True,
            )

    async def _on_remove(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if self._is_overall_selected():
            await interaction.response.defer(ephemeral=True)
            async with get_session() as session:
                overall = await select_repo.get_overall_comment(
                    session, self._kukai.id, self._selector_user_id
                )
                if overall:
                    await session.delete(overall)
            await self._refresh(interaction)
            return
        ps = self._selected_ps()
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self._kukai.id, interaction.guild.id)
                await select_service.remove_select(
                    session, kukai, self._selector_user_id, ps.submission_id
                )
            await self._refresh(interaction)
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
        except Exception:
            logger.exception("SelectView._on_remove failed")
            await interaction.followup.send(
                embed=error_embed("選句取消中に内部エラーが発生しました。ログを確認してください。"),
                ephemeral=True,
            )

    async def _on_overall(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            OverallSelectCommentModal(self._kukai.id, self._selector_user_id, self._overall_comment)
        )

    async def _on_done(self, interaction: discord.Interaction) -> None:
        label_map = {lbl.id: lbl for lbl in self._labels}
        selected_rows = []
        for ps in self._pub_subs:
            selected = self._selects.get(ps.submission_id)
            if selected is not None:
                selected_rows.append((selected, ps))

        if not selected_rows:
            desc = "まだ選句していません。"
        else:
            lines = []
            for selected, ps in selected_rows:
                label_name = "作者コメント" if selected.is_self_comment else (
                    label_map.get(selected.select_label_id).label if label_map.get(selected.select_label_id) else "?"
                )
                comment_part = ""
                if selected.comment:
                    comment_part = f" — {discord_safe(selected.comment.comment[:SELECT_COMMENT_PREVIEW_LIMIT])}"
                lines.append(f"No.{ps.number} **{label_name}**{comment_part}")
            if self._overall_comment:
                lines.append(f"総評 — {discord_safe(self._overall_comment[:OVERALL_COMMENT_PREVIEW_LIMIT])}")
            desc = "\n".join(lines[:40])

        embed = discord.Embed(
            title=f"選句一覧 — {self._kukai.title}",
            description=desc,
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"合計 {len(selected_rows)} 件")
        await interaction.response.edit_message(embed=embed, view=None)
