"""Submission UI: /submit view with Add, Edit, Delete buttons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from bot.database import get_session
from bot.services import kukai_service, submission_service
from bot.services.errors import ServiceError
from bot.utils.embed_builder import COLOR_INFO, COLOR_WARNING, error_embed
from bot.utils.submission_markup import discord_safe_submission_text, render_submission_for_discord

if TYPE_CHECKING:
    from bot.models.submission import Submission

DISCORD_TEXT_INPUT_MAX_LENGTH = 4000
SUBMISSION_TEXT_MAX_LENGTH = 500


@dataclass(frozen=True)
class BulkSubmissionResult:
    accepted: int
    over_limit_count: int
    submissions: list[Submission]


def _submissions_embed(kukai, subs: list[Submission]) -> discord.Embed:
    count = len(subs)
    limit = kukai.submission_max
    over = limit is not None and count > limit

    if subs:
        lines = [f"`{i + 1}.` {discord_safe_submission_text(s.text)}" for i, s in enumerate(subs)]
        desc = "\n".join(lines)
    else:
        desc = "まだ投句していません。"

    embed = discord.Embed(
        title=f"📝 投句 — {kukai.title}",
        description=desc,
        color=COLOR_WARNING if over else COLOR_INFO,
    )
    embed.add_field(
        name="投句数",
        value=f"**{count}** / {'∞' if limit is None else limit}{'　⚠️ 上限超過' if over else ''}",
        inline=True,
    )
    footer = f"句会 ID: {kukai.id}　|　最小: {kukai.submission_min}句"
    embed.set_footer(text=footer)
    return embed


async def submit_bulk_poems(
    session,
    kukai,
    user_id: int,
    poems: list[str],
    *,
    haigo: str | None = None,
) -> BulkSubmissionResult:
    accepted = 0
    over_limit_count = 0
    for poem in poems:
        _, over_limit = await submission_service.submit(session, kukai, user_id, poem, haigo=haigo)
        accepted += 1
        if over_limit:
            over_limit_count += 1

    subs = await submission_service.list_user_submissions(session, kukai.id, user_id)
    return BulkSubmissionResult(
        accepted=accepted,
        over_limit_count=over_limit_count,
        submissions=subs,
    )


def build_bulk_submission_embed(kukai, result: BulkSubmissionResult) -> discord.Embed:
    embed = _submissions_embed(kukai, result.submissions)
    embed.description = f"{result.accepted}句を登録しました。\n\n{embed.description or ''}"
    if result.over_limit_count:
        embed.description += (
            f"\n⚠️ {result.over_limit_count}句は上限（{kukai.submission_max}句）超過扱いです。"
        )
    return embed


def parse_bulk_submission_lines(text: str, *, remaining_limit: int | None) -> list[str]:
    poems = [line.strip() for line in text.splitlines() if line.strip()]
    if not poems:
        raise ValueError("少なくとも1句は入力してください。")
    for index, poem in enumerate(poems, start=1):
        if len(poem) > SUBMISSION_TEXT_MAX_LENGTH:
            raise ValueError(f"{index}行目: 1句は{SUBMISSION_TEXT_MAX_LENGTH}文字までです。")
    if remaining_limit is not None and len(poems) > remaining_limit:
        excess = len(poems) - remaining_limit
        raise ValueError(f"投句上限を超えています（残り{remaining_limit}句、{excess}句超過）。")
    return poems


def _submission_snapshot(subs: list[Submission]) -> str:
    if not subs:
        return "（未登録）"
    lines = [f"`{i + 1}.` {discord_safe_submission_text(s.text, limit=80)}" for i, s in enumerate(subs[:10])]
    if len(subs) > 10:
        lines.append(f"...他 {len(subs) - 10} 句")
    return "\n".join(lines)


async def _send_submission_status_message(
    interaction: discord.Interaction,
    *,
    title: str,
    subs: list[Submission],
) -> None:
    await interaction.followup.send(
        embed=discord.Embed(
            title=title,
            description=_submission_snapshot(subs),
            color=COLOR_INFO,
        ),
        ephemeral=True,
    )


class SubmitBulkModal(discord.ui.Modal):
    def __init__(
        self,
        kukai_id: int,
        slots: int | None,
        *,
        collect_haigo: bool = False,
        current_haigo: str | None = None,
    ) -> None:
        super().__init__(title="投句（追加）")
        self.kukai_id = kukai_id
        self._remaining_limit = None if slots is None else max(0, slots)
        self._haigo_input: discord.ui.TextInput | None = None
        if collect_haigo:
            self._haigo_input = discord.ui.TextInput(
                label="俳号（任意）",
                placeholder="空欄の場合はサーバーの表示名を使用します",
                required=False,
                max_length=100,
                default=current_haigo,
            )
            self.add_item(self._haigo_input)
        limit_note = f"・残り{self._remaining_limit}句" if self._remaining_limit is not None else ""
        self._poems_input = discord.ui.TextInput(
            label=f"投句（1行1句{limit_note}）",
            style=discord.TextStyle.paragraph,
            placeholder="1行に1句ずつ入力してください",
            max_length=DISCORD_TEXT_INPUT_MAX_LENGTH,
            required=True,
        )
        self.add_item(self._poems_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None

        try:
            poems = parse_bulk_submission_lines(
                str(self._poems_input.value),
                remaining_limit=self._remaining_limit,
            )
        except ValueError as error:
            await interaction.followup.send(
                embed=error_embed(str(error)),
                ephemeral=True,
            )
            return

        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                haigo = self._haigo_input.value.strip() if self._haigo_input is not None else None
                result = await submit_bulk_poems(
                    session,
                    kukai,
                    interaction.user.id,
                    poems,
                    haigo=haigo,
                )
            embed = build_bulk_submission_embed(kukai, result)
            await interaction.edit_original_response(
                embed=embed,
                view=SubmissionView(self.kukai_id, result.submissions, kukai),
            )
            await _send_submission_status_message(
                interaction,
                title="✅ 投句を登録しました",
                subs=result.submissions,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


# ── Edit modal ───────────────────────────────────────────────────────────

class SubmitEditModal(discord.ui.Modal, title="投句（編集）"):
    def __init__(self, kukai_id: int, submission_id: int, current_text: str) -> None:
        super().__init__()
        self.kukai_id = kukai_id
        self.submission_id = submission_id
        self._text = discord.ui.TextInput(
            label="俳句",
            style=discord.TextStyle.paragraph,
            max_length=500,
            default=current_text,
        )
        self.add_item(self._text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                await submission_service.edit(
                    session, kukai, interaction.user.id, self.submission_id, self._text.value
                )
                subs = await submission_service.list_user_submissions(
                    session, kukai.id, interaction.user.id
                )
            await interaction.edit_original_response(
                embed=_submissions_embed(kukai, subs),
                view=SubmissionView(self.kukai_id, subs, kukai),
            )
            await _send_submission_status_message(
                interaction,
                title="✅ 投句を変更しました",
                subs=subs,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


# ── Edit select (multi-submission) ───────────────────────────────────────

class SubmissionEditSelect(discord.ui.Select):
    def __init__(self, kukai_id: int, subs: list[Submission]) -> None:
        self.kukai_id = kukai_id
        self._sub_map = {str(s.id): s for s in subs}
        options = [
            discord.SelectOption(
                label=f"{i + 1}. {render_submission_for_discord(s.text)[:80]}",
                value=str(s.id),
            )
            for i, s in enumerate(subs[:25])
        ]
        super().__init__(
            placeholder="編集する句を選んでください…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        sub = self._sub_map[self.values[0]]
        await interaction.response.send_modal(
            SubmitEditModal(self.kukai_id, sub.id, sub.text)
        )


# ── Delete select (multi-submission) ─────────────────────────────────────

class SubmissionDeleteSelect(discord.ui.Select):
    def __init__(self, kukai_id: int, subs: list[Submission]) -> None:
        self.kukai_id = kukai_id
        options = [
            discord.SelectOption(
                label=f"{i + 1}. {render_submission_for_discord(s.text)[:80]}",
                value=str(s.id),
            )
            for i, s in enumerate(subs[:25])
        ]
        super().__init__(
            placeholder="削除する句を選んでください…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        sub_id = int(self.values[0])
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                await submission_service.delete_submission(
                    session, kukai, interaction.user.id, sub_id
                )
                subs = await submission_service.list_user_submissions(
                    session, kukai.id, interaction.user.id
                )
            await interaction.edit_original_response(
                embed=_submissions_embed(kukai, subs),
                view=SubmissionView(self.kukai_id, subs, kukai),
            )
            await _send_submission_status_message(
                interaction,
                title="✅ 投句を削除しました",
                subs=subs,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


# ── Main view ─────────────────────────────────────────────────────────────

class SubmissionView(discord.ui.View):
    def __init__(self, kukai_id: int, subs: list[Submission], kukai=None) -> None:
        super().__init__(timeout=300)
        self.kukai_id = kukai_id
        self._subs = list(subs)
        self._kukai = kukai
        has_subs = bool(subs)
        current_count = len(subs)
        limit = kukai.submission_max if kukai is not None else None
        remaining = None if limit is None else max(0, limit - current_count)
        add_slots = None if remaining is None else remaining

        add_btn = discord.ui.Button(
            label="追加",
            style=discord.ButtonStyle.success,
            disabled=(add_slots is not None and add_slots <= 0),
        )
        add_btn.callback = self._on_add

        edit_btn = discord.ui.Button(
            label="編集",
            style=discord.ButtonStyle.primary,
            disabled=not has_subs,
        )
        edit_btn.callback = self._on_edit

        del_btn = discord.ui.Button(
            label="削除",
            style=discord.ButtonStyle.danger,
            disabled=not has_subs,
        )
        del_btn.callback = self._on_delete

        self.add_item(del_btn)
        self.add_item(edit_btn)
        self.add_item(add_btn)
        self._add_slots = add_slots

    async def _on_add(self, interaction: discord.Interaction) -> None:
        if self._add_slots is not None and self._add_slots <= 0:
            await interaction.response.send_message(
                embed=error_embed("投句上限に達しているため、これ以上追加できません。"),
                ephemeral=True,
            )
            return
        collect_haigo = bool(self._kukai is not None and not self._kukai.entry_enabled)
        current_haigo = None
        if collect_haigo:
            assert interaction.guild is not None
            async with get_session() as session:
                profile = await submission_service.get_participant_profile(
                    session, self.kukai_id, interaction.user.id
                )
                current_haigo = profile.haigo if profile is not None else None
        await interaction.response.send_modal(
            SubmitBulkModal(
                self.kukai_id,
                self._add_slots,
                collect_haigo=collect_haigo,
                current_haigo=current_haigo,
            )
        )

    async def _on_edit(self, interaction: discord.Interaction) -> None:
        if not self._subs:
            await interaction.response.send_message(
                embed=error_embed("投句がありません。"), ephemeral=True
            )
            return
        if len(self._subs) == 1:
            s = self._subs[0]
            await interaction.response.send_modal(SubmitEditModal(self.kukai_id, s.id, s.text))
        else:
            view = discord.ui.View(timeout=120)
            view.add_item(SubmissionEditSelect(self.kukai_id, self._subs))
            await interaction.response.edit_message(
                embed=discord.Embed(description="編集する句を選んでください。", color=COLOR_INFO),
                view=view,
            )

    async def _on_delete(self, interaction: discord.Interaction) -> None:
        if not self._subs:
            await interaction.response.send_message(
                embed=error_embed("投句がありません。"), ephemeral=True
            )
            return
        assert interaction.guild is not None
        if len(self._subs) == 1:
            sub = self._subs[0]
            await interaction.response.defer(ephemeral=True)
            try:
                async with get_session() as session:
                    kukai = await kukai_service.get_kukai(
                        session, self.kukai_id, interaction.guild.id
                    )
                    await submission_service.delete_submission(
                        session, kukai, interaction.user.id, sub.id
                    )
                    subs = await submission_service.list_user_submissions(
                        session, kukai.id, interaction.user.id
                    )
                await interaction.edit_original_response(
                    embed=_submissions_embed(kukai, subs),
                    view=SubmissionView(self.kukai_id, subs, kukai),
                )
                await _send_submission_status_message(
                    interaction,
                    title="✅ 投句を削除しました",
                    subs=subs,
                )
            except ServiceError as e:
                await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
        else:
            view = discord.ui.View(timeout=120)
            view.add_item(SubmissionDeleteSelect(self.kukai_id, self._subs))
            await interaction.response.edit_message(
                embed=discord.Embed(description="削除する句を選んでください。", color=COLOR_INFO),
                view=view,
            )


# ── Rollback confirmation view ────────────────────────────────────────────

class RollbackView(discord.ui.View):
    """Confirm rollback with data retention options."""

    def __init__(self, *, allow_reset_submissions: bool) -> None:
        super().__init__(timeout=60)
        self.choice: str | None = None
        self._add_choice_button(
            label="投句保持・選句リセット",
            style=discord.ButtonStyle.danger,
            choice="reset_selects",
        )
        if allow_reset_submissions:
            self._add_choice_button(
                label="投句・選句をリセット",
                style=discord.ButtonStyle.danger,
                choice="reset_all",
            )
        self._add_choice_button(
            label="投句・選句を保持",
            style=discord.ButtonStyle.primary,
            choice="keep_all",
        )
        self._add_choice_button(
            label="キャンセル",
            style=discord.ButtonStyle.secondary,
            choice=None,
        )

    def _add_choice_button(
        self,
        *,
        label: str,
        style: discord.ButtonStyle,
        choice: str | None,
    ) -> None:
        button = discord.ui.Button(label=label, style=style)

        async def _callback(interaction: discord.Interaction) -> None:
            self.choice = choice
            self.stop()
            await interaction.response.defer()

        button.callback = _callback
        self.add_item(button)
