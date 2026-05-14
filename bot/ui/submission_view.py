"""Submission UI: /submit view with Add, Edit, Delete buttons."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.database import get_session
from bot.services import kukai_service, submission_service
from bot.services.errors import ServiceError
from bot.utils.embed_builder import COLOR_INFO, COLOR_WARNING, error_embed
from bot.utils.text import discord_safe

if TYPE_CHECKING:
    from bot.models.submission import Submission

GUI_BULK_LIMIT = 5


def _submissions_embed(kukai, subs: list[Submission]) -> discord.Embed:
    count = len(subs)
    limit = kukai.submission_max
    over = limit is not None and count > limit

    if subs:
        lines = [f"`{i + 1}.` {discord_safe(s.text)}" for i, s in enumerate(subs)]
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
    if kukai.submission_max is None or kukai.submission_max > GUI_BULK_LIMIT:
        footer += f"　|　GUIでは一度に{GUI_BULK_LIMIT}句まで追加できます"
    embed.set_footer(text=footer)
    return embed


class SubmitBulkModal(discord.ui.Modal):
    def __init__(self, kukai_id: int, slots: int) -> None:
        super().__init__(title="投句（追加）")
        self.kukai_id = kukai_id
        self._inputs: list[discord.ui.TextInput] = []
        safe_slots = max(1, min(GUI_BULK_LIMIT, slots))
        for index in range(safe_slots):
            item = discord.ui.TextInput(
                label=f"俳句{index + 1}",
                style=discord.TextStyle.paragraph,
                max_length=500,
                required=(index == 0),
            )
            self.add_item(item)
            self._inputs.append(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None

        poems = [item.value.strip() for item in self._inputs if item.value.strip()]
        if not poems:
            await interaction.followup.send(
                embed=error_embed("少なくとも1句は入力してください。"),
                ephemeral=True,
            )
            return

        accepted = 0
        over_limit_count = 0
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                for poem in poems:
                    _, over_limit = await submission_service.submit(
                        session, kukai, interaction.user.id, poem
                    )
                    accepted += 1
                    if over_limit:
                        over_limit_count += 1
                subs = await submission_service.list_user_submissions(
                    session, kukai.id, interaction.user.id
                )
            embed = _submissions_embed(kukai, subs)
            embed.description = (
                f"{accepted}句を追加しました。\n\n{embed.description or ''}"
            )
            if over_limit_count:
                embed.description += (
                    f"\n⚠️ {over_limit_count}句は上限（{kukai.submission_max}句）超過扱いです。"
                )
            await interaction.edit_original_response(
                embed=embed,
                view=SubmissionView(self.kukai_id, subs, kukai),
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
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


# ── Edit select (multi-submission) ───────────────────────────────────────

class SubmissionEditSelect(discord.ui.Select):
    def __init__(self, kukai_id: int, subs: list[Submission]) -> None:
        self.kukai_id = kukai_id
        self._sub_map = {str(s.id): s for s in subs}
        options = [
            discord.SelectOption(
                label=f"{i + 1}. {s.text[:80]}",
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
                label=f"{i + 1}. {s.text[:80]}",
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
        add_slots = GUI_BULK_LIMIT if remaining is None else min(GUI_BULK_LIMIT, remaining)

        add_btn = discord.ui.Button(
            label="追加",
            style=discord.ButtonStyle.success,
            disabled=(add_slots <= 0),
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

        self.add_item(add_btn)
        self.add_item(edit_btn)
        self.add_item(del_btn)
        self._add_slots = add_slots

    async def _on_add(self, interaction: discord.Interaction) -> None:
        if self._add_slots <= 0:
            await interaction.response.send_message(
                embed=error_embed("投句上限に達しているため、これ以上追加できません。"),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(SubmitBulkModal(self.kukai_id, self._add_slots))

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
    """Confirm rollback with option to reset selects."""

    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.choice: str | None = None  # 'keep_selects' | 'reset_selects' | None (cancelled)

    @discord.ui.button(label="選句を保持して戻す", style=discord.ButtonStyle.primary)
    async def keep_selects(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.choice = "keep_selects"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="選句もリセットして戻す", style=discord.ButtonStyle.danger)
    async def reset_selects(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.choice = "reset_selects"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.choice = None
        self.stop()
        await interaction.response.defer()
