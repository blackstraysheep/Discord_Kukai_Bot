"""Experimental selection interfaces used by the /select-lab command group."""

from __future__ import annotations

import logging

import discord

from bot.database import get_session
from bot.services import kukai_service, select_lab_service, select_service
from bot.services.errors import ServiceError, ValidationError
from bot.ui.select_view import _select_snapshot
from bot.utils.embed_builder import COLOR_INFO, COLOR_SUCCESS, error_embed
from bot.utils.submission_markup import discord_safe_submission_text, render_submission_for_discord
from bot.utils.text import discord_safe

logger = logging.getLogger(__name__)
_PAGE_SIZE = 25


def _submission_index(data: select_lab_service.SelectLabData, submission_id: int) -> int:
    return next(
        (index for index, item in enumerate(data.submissions) if item.submission_id == submission_id),
        0,
    )


def _count_summary(data: select_lab_service.SelectLabData) -> str:
    counts: dict[int, int] = {}
    for selected in data.selects_by_submission.values():
        if not selected.is_self_comment:
            counts[selected.select_label_id] = counts.get(selected.select_label_id, 0) + 1
    rows = []
    for label in data.normal_labels:
        maximum = "∞" if label.max_count is None else str(label.max_count)
        target = f"{label.min_count}〜{maximum}" if label.min_count else maximum
        rows.append(f"{label.label}: **{counts.get(label.id, 0)}** / {target}")
    return "\n".join(rows) or "（通常選句ラベルなし）"


async def _reload(kukai_id: int, guild_id: int, user_id: int):
    async with get_session() as session:
        kukai = await kukai_service.get_kukai(session, kukai_id, guild_id)
        data = await select_lab_service.load_lab_data(session, kukai_id, user_id)
    return kukai, data


class LabOverallModal(discord.ui.Modal, title="総評を編集"):
    def __init__(self, kukai_id: int, user_id: int, current: str, *, view_kind: str, state: dict):
        super().__init__()
        self.kukai_id = kukai_id
        self.user_id = user_id
        self.view_kind = view_kind
        self.state = state
        self.text_input = discord.ui.TextInput(
            label="総評（空欄で削除）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=2000,
            default=current or None,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                value = self.text_input.value.strip()
                if value:
                    await select_service.set_overall_comment(session, kukai, self.user_id, value)
                else:
                    await select_lab_service.clear_overall_comment(session, kukai, self.user_id)
            await refresh_lab_message(interaction, self.view_kind, self.kukai_id, self.user_id, self.state)
        except ServiceError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)


class LabCommentModal(discord.ui.Modal):
    def __init__(
        self,
        kukai_id: int,
        user_id: int,
        submission_id: int,
        label_id: int,
        *,
        required: bool,
        is_self_comment: bool,
        current: str,
        view_kind: str,
        state: dict,
        advance_review: bool = False,
    ) -> None:
        super().__init__(title="作者コメント" if is_self_comment else "選評を編集")
        self.kukai_id = kukai_id
        self.user_id = user_id
        self.submission_id = submission_id
        self.label_id = label_id
        self.is_self_comment = is_self_comment
        self.view_kind = view_kind
        self.state = state
        self.advance_review = advance_review
        self.text_input = discord.ui.TextInput(
            label="コメント" + ("（必須）" if required else "（任意）"),
            style=discord.TextStyle.paragraph,
            required=required,
            max_length=500,
            default=current or None,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                await select_service.cast_select(
                    session,
                    kukai,
                    self.user_id,
                    self.submission_id,
                    self.label_id,
                    comment=self.text_input.value or None,
                    is_self_comment=self.is_self_comment,
                )
                data = await select_lab_service.load_lab_data(session, kukai.id, self.user_id)
            state = dict(self.state)
            if self.view_kind == "review":
                state["last_submission_id"] = self.submission_id
                if self.advance_review:
                    state["index"] = ReviewSelectView.next_unprocessed_index(
                        data, self.user_id, _submission_index(data, self.submission_id)
                    )
            await refresh_lab_message(interaction, self.view_kind, self.kukai_id, self.user_id, state)
        except ServiceError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
        except Exception:
            logger.exception("LabCommentModal.on_submit failed")
            await interaction.followup.send(embed=error_embed("選句保存中に内部エラーが発生しました。"), ephemeral=True)


async def refresh_lab_message(
    interaction: discord.Interaction,
    view_kind: str,
    kukai_id: int,
    user_id: int,
    state: dict,
) -> None:
    assert interaction.guild is not None
    kukai, data = await _reload(kukai_id, interaction.guild.id, user_id)
    if view_kind == "review":
        view = ReviewSelectView(
            kukai,
            data,
            user_id,
            index=int(state.get("index", 0)),
            last_submission_id=state.get("last_submission_id"),
        )
    else:
        view = BatchSelectView(
            kukai,
            data,
            user_id,
            page=int(state.get("page", 0)),
            label_id=state.get("label_id"),
        )
    await interaction.edit_original_response(embed=view.build_embed(), view=view)


class _ReviewSubmissionSelect(discord.ui.Select):
    def __init__(self, owner: "ReviewSelectView"):
        self.owner = owner
        start = owner.page * _PAGE_SIZE
        options = [
            discord.SelectOption(
                label=f"No.{item.number}",
                value=str(index),
                description=render_submission_for_discord(item.submission.text)[:95] or "（空）",
                default=index == owner.index,
            )
            for index, item in enumerate(owner.data.submissions[start : start + _PAGE_SIZE], start=start)
        ]
        super().__init__(placeholder="表示する句を選択", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.owner.index = int(self.values[0])
        self.owner.rebuild()
        await interaction.response.edit_message(embed=self.owner.build_embed(), view=self.owner)


class _ReviewLabelSelect(discord.ui.Select):
    def __init__(self, owner: "ReviewSelectView"):
        self.owner = owner
        options = [
            discord.SelectOption(label=label.label, value=str(label.id), description=f"{label.point:+d}点")
            for label in owner.data.normal_labels[:25]
        ]
        super().__init__(placeholder="選句種別を選ぶと即時保存", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        label = next(label for label in self.owner.data.normal_labels if label.id == int(self.values[0]))
        await self.owner.save_label(interaction, label)


class _ReviewCommentSelect(discord.ui.Select):
    def __init__(self, owner: "ReviewSelectView"):
        self.owner = owner
        eligible = owner.comment_targets()[:25]
        options = [
            discord.SelectOption(
                label=f"No.{item.number}",
                value=str(item.submission_id),
                description="作者コメント" if selected.is_self_comment else selected.select_label.label,
            )
            for item, selected in eligible
        ]
        super().__init__(placeholder="登録済みの選評を追加・編集", options=options, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner.open_comment(interaction, int(self.values[0]), advance=False)


class ReviewSelectView(discord.ui.View):
    def __init__(self, kukai, data: select_lab_service.SelectLabData, user_id: int, *, index: int = 0, last_submission_id=None):
        super().__init__(timeout=600)
        self.kukai = kukai
        self.data = data
        self.user_id = user_id
        self.index = min(max(index, 0), max(len(data.submissions) - 1, 0))
        self.last_submission_id = last_submission_id
        self.rebuild()

    @property
    def page(self) -> int:
        return self.index // _PAGE_SIZE

    @property
    def current(self):
        return self.data.submissions[self.index]

    @staticmethod
    def next_unprocessed_index(data, user_id: int, current_index: int) -> int:
        count = len(data.submissions)
        for offset in range(1, count + 1):
            index = (current_index + offset) % count
            item = data.submissions[index]
            selected = data.selects_by_submission.get(item.submission_id)
            if selected is None:
                return index
        return current_index

    def state(self) -> dict:
        return {"index": self.index, "last_submission_id": self.last_submission_id}

    def build_embed(self) -> discord.Embed:
        item = self.current
        selected = self.data.selects_by_submission.get(item.submission_id)
        own = item.submission.user_id == self.user_id
        if selected:
            label = select_lab_service.AUTHOR_COMMENT_LABEL if selected.is_self_comment else selected.select_label.label
            status = f"登録済み: **{label}**"
            if selected.comment:
                status += f"\n> {discord_safe(selected.comment.comment[:300])}"
        else:
            status = "未処理" + ("（自句）" if own else "")
        embed = discord.Embed(title=f"選句Lab・1句ずつ — {self.kukai.title}", color=COLOR_INFO)
        embed.description = f"### No.{item.number}\n```\n{render_submission_for_discord(item.submission.text)}\n```\n{status}"
        embed.add_field(name="進捗", value=_count_summary(self.data), inline=False)
        embed.set_footer(text=f"{self.index + 1}/{len(self.data.submissions)} ・ 種別操作で即時保存")
        return embed

    def comment_targets(self):
        rows = []
        for item in self.data.submissions:
            selected = self.data.selects_by_submission.get(item.submission_id)
            if selected is None:
                continue
            if selected.is_self_comment or selected.select_label.comment_mode in {"optional", "required"}:
                rows.append((item, selected))
        return rows

    def rebuild(self) -> None:
        self.clear_items()
        self.add_item(_ReviewSubmissionSelect(self))
        own = self.current.submission.user_id == self.user_id
        if own:
            button = discord.ui.Button(label="作者コメント", style=discord.ButtonStyle.primary, row=1)
            button.callback = self._author_comment
            self.add_item(button)
        elif len(self.data.normal_labels) <= 5:
            for label in self.data.normal_labels:
                button = discord.ui.Button(label=label.label, style=discord.ButtonStyle.primary, row=1)
                button.callback = self._label_callback(label)
                self.add_item(button)
        else:
            self.add_item(_ReviewLabelSelect(self))

        remove = discord.ui.Button(label="取消", style=discord.ButtonStyle.danger, row=2)
        remove.callback = self._remove
        self.add_item(remove)
        previous = discord.ui.Button(label="前へ", style=discord.ButtonStyle.secondary, row=2, disabled=self.index == 0)
        previous.callback = self._previous
        self.add_item(previous)
        following = discord.ui.Button(
            label="次へ", style=discord.ButtonStyle.secondary, row=2, disabled=self.index >= len(self.data.submissions) - 1
        )
        following.callback = self._following
        self.add_item(following)
        overall = discord.ui.Button(label="総評", style=discord.ButtonStyle.secondary, row=2)
        overall.callback = self._overall
        self.add_item(overall)
        done = discord.ui.Button(label="完了", style=discord.ButtonStyle.success, row=2)
        done.callback = self._done
        self.add_item(done)

        if self.comment_targets():
            self.add_item(_ReviewCommentSelect(self))
        if self.last_submission_id is not None:
            last = self.data.selects_by_submission.get(self.last_submission_id)
            if last and (last.is_self_comment or last.select_label.comment_mode in {"optional", "required"}):
                quick = discord.ui.Button(label="直前の句に選評", style=discord.ButtonStyle.secondary, row=4)
                quick.callback = self._last_comment
                self.add_item(quick)

    def _label_callback(self, label):
        async def callback(interaction: discord.Interaction):
            await self.save_label(interaction, label)
        return callback

    async def save_label(self, interaction: discord.Interaction, label) -> None:
        if label.comment_mode == "required":
            await self.open_comment(interaction, self.current.submission_id, label=label, advance=True)
            return
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            existing = self.data.selects_by_submission.get(self.current.submission_id)
            preserved_comment = None
            if label.comment_mode == "optional" and existing is not None and existing.comment:
                preserved_comment = existing.comment.comment
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai.id, interaction.guild.id)
                await select_service.cast_select(
                    session,
                    kukai,
                    self.user_id,
                    self.current.submission_id,
                    label.id,
                    comment=preserved_comment,
                )
                data = await select_lab_service.load_lab_data(session, kukai.id, self.user_id)
            self.last_submission_id = self.current.submission_id
            self.index = self.next_unprocessed_index(data, self.user_id, self.index)
            self.data = data
            self.rebuild()
            await interaction.edit_original_response(embed=self.build_embed(), view=self)
        except ServiceError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)

    async def open_comment(self, interaction, submission_id: int, *, label=None, advance=False):
        selected = self.data.selects_by_submission.get(submission_id)
        if label is None:
            if selected is None:
                await interaction.response.send_message(embed=error_embed("先に選句してください。"), ephemeral=True)
                return
            label = selected.select_label
        current = selected.comment.comment if selected and selected.comment else ""
        item = next(item for item in self.data.submissions if item.submission_id == submission_id)
        is_self = item.submission.user_id == self.user_id
        await interaction.response.send_modal(
            LabCommentModal(
                self.kukai.id,
                self.user_id,
                submission_id,
                self.data.author_label.id if is_self else label.id,
                required=is_self or label.comment_mode == "required",
                is_self_comment=is_self,
                current=current,
                view_kind="review",
                state=self.state(),
                advance_review=advance,
            )
        )

    async def _author_comment(self, interaction):
        await self.open_comment(interaction, self.current.submission_id, label=self.data.author_label, advance=True)

    async def _last_comment(self, interaction):
        await self.open_comment(interaction, self.last_submission_id, advance=False)

    async def _remove(self, interaction):
        selected = self.data.selects_by_submission.get(self.current.submission_id)
        if selected is None:
            await interaction.response.send_message(embed=error_embed("この句には登録がありません。"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai.id, interaction.guild.id)
                await select_service.remove_select(session, kukai, self.user_id, self.current.submission_id)
            await refresh_lab_message(interaction, "review", self.kukai.id, self.user_id, self.state())
        except ServiceError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)

    async def _previous(self, interaction):
        self.index -= 1
        self.rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _following(self, interaction):
        self.index += 1
        self.rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _overall(self, interaction):
        await interaction.response.send_modal(
            LabOverallModal(self.kukai.id, self.user_id, self.data.overall_comment, view_kind="review", state=self.state())
        )

    async def _done(self, interaction):
        embed = discord.Embed(
            title=f"選句Lab・確認 — {self.kukai.title}",
            description=_select_snapshot(self.data.submissions, self.data.selects_by_submission, self.data.overall_comment),
            color=COLOR_SUCCESS,
        )
        await interaction.response.edit_message(embed=embed, view=None)


class _BatchLabelSelect(discord.ui.Select):
    def __init__(self, owner: "BatchSelectView"):
        self.owner = owner
        options = [
            discord.SelectOption(label=label.label, value=str(label.id), default=label.id == owner.label.id)
            for label in owner.data.normal_labels[:25]
        ]
        super().__init__(placeholder="編集する選句種別", options=options, row=0)

    async def callback(self, interaction):
        self.owner.label_id = int(self.values[0])
        self.owner.rebuild()
        await interaction.response.edit_message(embed=self.owner.build_embed(), view=self.owner)


class _BatchAssignmentSelect(discord.ui.Select):
    def __init__(self, owner: "BatchSelectView"):
        self.owner = owner
        items = owner.page_items
        required = owner.label.comment_mode == "required"
        options = []
        for item in items:
            existing = owner.data.selects_by_submission.get(item.submission_id)
            options.append(
                discord.SelectOption(
                    label=f"No.{item.number}",
                    value=str(item.submission_id),
                    description=render_submission_for_discord(item.submission.text)[:95] or "（空）",
                    default=bool(
                        not required
                        and existing
                        and not existing.is_self_comment
                        and existing.select_label_id == owner.label.id
                    ),
                )
            )
        if not options:
            options = [discord.SelectOption(label="対象句なし", value="disabled")]
        maximum = 1 if required else min(len(options), owner.label.max_count or len(options), 25)
        super().__init__(
            placeholder="1句選んで必須選評を入力" if required else "この種別にする句を複数選択",
            options=options,
            min_values=1 if required else 0,
            max_values=max(1, maximum),
            disabled=options[0].value == "disabled",
            row=1,
        )

    async def callback(self, interaction):
        if self.owner.label.comment_mode == "required":
            await self.owner.open_comment(interaction, int(self.values[0]), self.owner.label)
            return
        selected_ids = {int(value) for value in self.values}
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.owner.kukai.id, interaction.guild.id)
                await select_lab_service.reconcile_batch_page(
                    session,
                    kukai,
                    self.owner.user_id,
                    self.owner.label,
                    {item.submission_id for item in self.owner.page_items},
                    selected_ids,
                )
            await refresh_lab_message(interaction, "batch", self.owner.kukai.id, self.owner.user_id, self.owner.state())
        except ServiceError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)


class _BatchCommentSelect(discord.ui.Select):
    def __init__(self, owner: "BatchSelectView"):
        self.owner = owner
        rows = []
        for item in owner.page_items:
            selected = owner.data.selects_by_submission.get(item.submission_id)
            if selected and not selected.is_self_comment and selected.select_label.comment_mode in {"optional", "required"}:
                rows.append((item, selected))
        options = [
            discord.SelectOption(label=f"No.{item.number}", value=str(item.submission_id), description=selected.select_label.label)
            for item, selected in rows[:25]
        ]
        super().__init__(placeholder="選評を追加・編集", options=options, row=2)

    async def callback(self, interaction):
        selected = self.owner.data.selects_by_submission[int(self.values[0])]
        await self.owner.open_comment(interaction, int(self.values[0]), selected.select_label)


class _BatchRemoveRequiredSelect(discord.ui.Select):
    def __init__(self, owner: "BatchSelectView"):
        self.owner = owner
        rows = [
            item for item in owner.page_items
            if (selected := owner.data.selects_by_submission.get(item.submission_id))
            and not selected.is_self_comment and selected.select_label_id == owner.label.id
        ]
        options = [discord.SelectOption(label=f"No.{item.number}", value=str(item.submission_id)) for item in rows]
        super().__init__(placeholder="この種別から取消", options=options, row=2)

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.owner.kukai.id, interaction.guild.id)
                await select_service.remove_select(session, kukai, self.owner.user_id, int(self.values[0]))
            await refresh_lab_message(interaction, "batch", self.owner.kukai.id, self.owner.user_id, self.owner.state())
        except ServiceError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)


class _AuthorTargetSelect(discord.ui.Select):
    def __init__(self, owner: "BatchSelectView"):
        self.owner = owner
        options = [
            discord.SelectOption(label=f"No.{item.number}", value=str(item.submission_id), description="作者コメントを入力")
            for item in owner.data.submissions if item.submission.user_id == owner.user_id
        ][:25]
        super().__init__(placeholder="自句へ作者コメント", options=options, row=3)

    async def callback(self, interaction):
        await self.owner.open_comment(interaction, int(self.values[0]), self.owner.data.author_label, is_self=True)


class BatchSelectView(discord.ui.View):
    def __init__(self, kukai, data: select_lab_service.SelectLabData, user_id: int, *, page: int = 0, label_id=None):
        super().__init__(timeout=600)
        self.kukai = kukai
        self.data = data
        self.user_id = user_id
        self.normal_submissions = [item for item in data.submissions if item.submission.user_id != user_id]
        self.page_count = max(1, (len(self.normal_submissions) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self.page = min(max(page, 0), self.page_count - 1)
        self.label_id = label_id if any(label.id == label_id for label in data.normal_labels) else data.normal_labels[0].id
        self.rebuild()

    @property
    def label(self):
        return next(label for label in self.data.normal_labels if label.id == self.label_id)

    @property
    def page_items(self):
        start = self.page * _PAGE_SIZE
        return self.normal_submissions[start : start + _PAGE_SIZE]

    def state(self):
        return {"page": self.page, "label_id": self.label_id}

    def build_embed(self):
        embed = discord.Embed(title=f"選句Lab・まとめて — {self.kukai.title}", color=COLOR_INFO)
        mode = "1句ずつ選評必須" if self.label.comment_mode == "required" else "複数選択で即時保存"
        embed.description = f"編集中: **{self.label.label}**（{mode}）\nチェックを外すと、このページの同種別選句を取り消します。"
        lines = [f"`No.{item.number}` {discord_safe_submission_text(item.submission.text, limit=70)}" for item in self.page_items]
        embed.add_field(name=f"句一覧 {self.page + 1}/{self.page_count}", value="\n".join(lines) or "（対象句なし）", inline=False)
        embed.add_field(name="進捗", value=_count_summary(self.data), inline=False)
        return embed

    def rebuild(self):
        self.clear_items()
        self.add_item(_BatchLabelSelect(self))
        self.add_item(_BatchAssignmentSelect(self))
        if self.label.comment_mode == "required":
            if any(
                (selected := self.data.selects_by_submission.get(item.submission_id))
                and not selected.is_self_comment and selected.select_label_id == self.label.id
                for item in self.page_items
            ):
                self.add_item(_BatchRemoveRequiredSelect(self))
        elif any(
            (selected := self.data.selects_by_submission.get(item.submission_id))
            and not selected.is_self_comment and selected.select_label.comment_mode in {"optional", "required"}
            for item in self.page_items
        ):
            self.add_item(_BatchCommentSelect(self))
        if any(item.submission.user_id == self.user_id for item in self.data.submissions):
            self.add_item(_AuthorTargetSelect(self))

        previous = discord.ui.Button(label="前へ", row=4, disabled=self.page == 0)
        previous.callback = self._previous
        self.add_item(previous)
        following = discord.ui.Button(label="次へ", row=4, disabled=self.page >= self.page_count - 1)
        following.callback = self._following
        self.add_item(following)
        overall = discord.ui.Button(label="総評", row=4)
        overall.callback = self._overall
        self.add_item(overall)
        done = discord.ui.Button(label="完了", style=discord.ButtonStyle.success, row=4)
        done.callback = self._done
        self.add_item(done)

    async def open_comment(self, interaction, submission_id: int, label, is_self=False):
        selected = self.data.selects_by_submission.get(submission_id)
        current = selected.comment.comment if selected and selected.comment else ""
        await interaction.response.send_modal(
            LabCommentModal(
                self.kukai.id,
                self.user_id,
                submission_id,
                label.id,
                required=is_self or label.comment_mode == "required",
                is_self_comment=is_self,
                current=current,
                view_kind="batch",
                state=self.state(),
            )
        )

    async def _previous(self, interaction):
        self.page -= 1
        self.rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _following(self, interaction):
        self.page += 1
        self.rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _overall(self, interaction):
        await interaction.response.send_modal(
            LabOverallModal(self.kukai.id, self.user_id, self.data.overall_comment, view_kind="batch", state=self.state())
        )

    async def _done(self, interaction):
        embed = discord.Embed(
            title=f"選句Lab・確認 — {self.kukai.title}",
            description=_select_snapshot(self.data.submissions, self.data.selects_by_submission, self.data.overall_comment),
            color=COLOR_SUCCESS,
        )
        await interaction.response.edit_message(embed=embed, view=None)


class SelectLabFormModal(discord.ui.Modal, title="選句Lab・全件編集"):
    def __init__(self, kukai, user_id: int, fields: tuple[str, str, str, str]):
        super().__init__()
        self.kukai_id = kukai.id
        self.user_id = user_id
        selections, comments, authors, overall = fields
        self.selections = discord.ui.TextInput(
            label="選句（ラベル=1,2・空欄で全取消）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=select_lab_service.FORM_INPUT_LIMIT,
            default=selections or None,
            placeholder="特選=3\n並選=1,4,8",
        )
        self.comments = discord.ui.TextInput(
            label="選評（任意・番号=本文）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=select_lab_service.FORM_INPUT_LIMIT,
            default=comments or None,
            placeholder=r"3=選評本文（改行は \n）",
        )
        self.authors = discord.ui.TextInput(
            label="作者コメント（任意・自句番号=本文）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=select_lab_service.FORM_INPUT_LIMIT,
            default=authors or None,
        )
        self.overall = discord.ui.TextInput(
            label="総評（任意・空欄で削除）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=2000,
            default=overall or None,
        )
        for item in (self.selections, self.comments, self.authors, self.overall):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            payload = select_lab_service.parse_form_payload(
                self.selections.value,
                self.comments.value,
                self.authors.value,
                self.overall.value,
            )
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                await select_lab_service.replace_from_form(session, kukai, self.user_id, payload)
                data = await select_lab_service.load_lab_data(session, kukai.id, self.user_id)
            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ 選句を全件更新しました",
                    description=_select_snapshot(data.submissions, data.selects_by_submission, data.overall_comment),
                    color=COLOR_SUCCESS,
                ),
                ephemeral=True,
            )
        except (ServiceError, ValueError) as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
        except Exception:
            logger.exception("SelectLabFormModal.on_submit failed")
            await interaction.followup.send(embed=error_embed("選句更新中に内部エラーが発生しました。"), ephemeral=True)


def build_review_response(kukai, data, user_id: int):
    if not data.submissions:
        raise ValidationError("公開済みの投句がありません。")
    if not data.normal_labels:
        raise ValidationError("通常選句ラベルが設定されていません。")
    if len(data.normal_labels) > 25:
        raise ValidationError("選句ラベルが25件を超えるため、このLab UIでは表示できません。")
    view = ReviewSelectView(kukai, data, user_id)
    return view.build_embed(), view


def build_batch_response(kukai, data, user_id: int):
    if not data.submissions:
        raise ValidationError("公開済みの投句がありません。")
    if not data.normal_labels:
        raise ValidationError("通常選句ラベルが設定されていません。")
    if len(data.normal_labels) > 25:
        raise ValidationError("選句ラベルが25件を超えるため、このLab UIでは表示できません。")
    view = BatchSelectView(kukai, data, user_id)
    return view.build_embed(), view
