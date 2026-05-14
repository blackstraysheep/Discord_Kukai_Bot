"""Voting UI: /select view showing published haiku one at a time."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from sqlalchemy import select

from bot.database import get_session
from bot.models.vote_rule import VoteLabel
from bot.repositories import submission_repo, vote_repo
from bot.services import kukai_service, vote_service
from bot.services.errors import ServiceError
from bot.utils.embed_builder import COLOR_INFO, COLOR_SUCCESS, error_embed
from bot.utils.text import discord_safe

if TYPE_CHECKING:
    from bot.models.submission import PublishedSubmission
    from bot.models.vote import Vote


# ── Data loader ──────────────────────────────────────────────────────────

async def load_vote_data(
    session, kukai_id: int, voter_user_id: int
) -> tuple[list[PublishedSubmission], list[VoteLabel], dict[int, Vote]]:
    pub_subs = await submission_repo.list_published(session, kukai_id)
    result = await session.execute(
        select(VoteLabel)
        .where(VoteLabel.kukai_id == kukai_id)
        .order_by(VoteLabel.display_order)
    )
    labels = list(result.scalars().all())
    votes = await vote_repo.get_votes_by_voter(session, kukai_id, voter_user_id)
    votes_by_sub = {v.submission_id: v for v in votes}
    return pub_subs, labels, votes_by_sub


# ── Embed builder ─────────────────────────────────────────────────────────

def _vote_embed(
    kukai,
    ps: PublishedSubmission,
    current_vote: Vote | None,
    labels: list[VoteLabel],
    idx: int,
    total: int,
    is_own: bool,
) -> discord.Embed:
    label_map = {lbl.id: lbl for lbl in labels}
    embed = discord.Embed(
        title=f"選句 — {kukai.title}",
        description=f"```\n{ps.submission.text}\n```",
        color=COLOR_INFO,
    )

    if is_own:
        status = "（自分の句）"
    elif current_vote:
        lbl = label_map.get(current_vote.vote_label_id)
        status = f"**{lbl.label}**" if lbl else "（不明）"
        if current_vote.comment:
            status += f"\n> {discord_safe(current_vote.comment.comment)}"
    else:
        status = "（未選択）"

    embed.add_field(name=f"No.{ps.number}", value=status, inline=False)
    embed.set_footer(text=f"{idx + 1} / {total} 句")
    return embed


# ── Comment modals ────────────────────────────────────────────────────────

class VoteCommentModal(discord.ui.Modal, title="コメント入力"):
    def __init__(
        self,
        kukai_id: int,
        submission_id: int,
        vote_label_id: int,
        label_name: str,
        required: bool,
        voter_user_id: int,
        current_idx: int,
    ) -> None:
        super().__init__()
        self.kukai_id = kukai_id
        self.submission_id = submission_id
        self.vote_label_id = vote_label_id
        self.voter_user_id = voter_user_id
        self.current_idx = current_idx
        self._comment = discord.ui.TextInput(
            label=f"コメント（{label_name}）",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=required,
        )
        self.add_item(self._comment)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(session, self.kukai_id, interaction.guild.id)
                await vote_service.cast_vote(
                    session,
                    kukai,
                    self.voter_user_id,
                    self.submission_id,
                    self.vote_label_id,
                    comment=self._comment.value or None,
                )
                pub_subs, labels, votes_by_sub = await load_vote_data(
                    session, kukai.id, self.voter_user_id
                )

            idx = min(self.current_idx, len(pub_subs) - 1)
            view = VoteView(kukai, pub_subs, labels, votes_by_sub, idx, self.voter_user_id)
            await interaction.edit_original_response(
                embed=view.build_embed(), view=view
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


class OverallCommentModal(discord.ui.Modal, title="総評を入力"):
    def __init__(self, kukai_id: int, voter_user_id: int, current_text: str = "") -> None:
        super().__init__()
        self.kukai_id = kukai_id
        self.voter_user_id = voter_user_id
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
                await vote_service.set_overall_comment(
                    session, kukai, self.voter_user_id, self._comment.value
                )
            await interaction.followup.send(
                embed=discord.Embed(description="総評を保存しました。", color=COLOR_SUCCESS),
                ephemeral=True,
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


# ── Label select ──────────────────────────────────────────────────────────

class VoteLabelSelect(discord.ui.Select):
    def __init__(
        self,
        kukai_id: int,
        ps: PublishedSubmission,
        labels: list[VoteLabel],
        current_vote: Vote | None,
        current_idx: int,
        voter_user_id: int,
    ) -> None:
        self.kukai_id = kukai_id
        self.submission_id = ps.submission_id
        self.current_idx = current_idx
        self.voter_user_id = voter_user_id
        self._label_map = {str(lbl.id): lbl for lbl in labels}

        options = []
        for lbl in labels:
            desc = f"{lbl.point:+d}pt"
            if lbl.max_count is not None:
                desc += f" | 最大{lbl.max_count}票"
            options.append(
                discord.SelectOption(
                    label=lbl.label,
                    value=str(lbl.id),
                    description=desc,
                    default=(current_vote is not None and current_vote.vote_label_id == lbl.id),
                )
            )

        current_label_name = ""
        if current_vote:
            matched = next((l for l in labels if l.id == current_vote.vote_label_id), None)
            current_label_name = f"選択中: {matched.label}" if matched else ""

        super().__init__(
            placeholder=current_label_name or "ラベルを選択…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        label = self._label_map[self.values[0]]

        if label.comment_mode in ("optional", "required"):
            await interaction.response.send_modal(
                VoteCommentModal(
                    kukai_id=self.kukai_id,
                    submission_id=self.submission_id,
                    vote_label_id=label.id,
                    label_name=label.label,
                    required=(label.comment_mode == "required"),
                    voter_user_id=self.voter_user_id,
                    current_idx=self.current_idx,
                )
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(
                    session, self.kukai_id, interaction.guild.id
                )
                await vote_service.cast_vote(
                    session, kukai, self.voter_user_id, self.submission_id, label.id
                )
                pub_subs, labels, votes_by_sub = await load_vote_data(
                    session, kukai.id, self.voter_user_id
                )

            idx = min(self.current_idx, len(pub_subs) - 1)
            view = VoteView(kukai, pub_subs, labels, votes_by_sub, idx, self.voter_user_id)
            await interaction.edit_original_response(
                embed=view.build_embed(), view=view
            )
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)


# ── Main view ─────────────────────────────────────────────────────────────

class VoteView(discord.ui.View):
    def __init__(
        self,
        kukai,
        pub_subs: list[PublishedSubmission],
        labels: list[VoteLabel],
        votes_by_sub: dict[int, Vote],
        idx: int,
        voter_user_id: int,
    ) -> None:
        super().__init__(timeout=600)
        self._kukai = kukai
        self._pub_subs = pub_subs
        self._labels = labels
        self._votes = votes_by_sub
        self._idx = idx
        self._voter = voter_user_id
        self._build()

    # ------------------------------------------------------------------

    def build_embed(self) -> discord.Embed:
        ps = self._pub_subs[self._idx]
        is_own = ps.submission.user_id == self._voter
        return _vote_embed(
            self._kukai,
            ps,
            self._votes.get(ps.submission_id),
            self._labels,
            self._idx,
            len(self._pub_subs),
            is_own,
        )

    def _build(self) -> None:
        self.clear_items()
        ps = self._pub_subs[self._idx]
        is_own = ps.submission.user_id == self._voter
        current_vote = self._votes.get(ps.submission_id)
        total = len(self._pub_subs)

        if not is_own:
            self.add_item(
                VoteLabelSelect(
                    self._kukai.id, ps, self._labels, current_vote,
                    self._idx, self._voter
                )
            )

        prev_btn = discord.ui.Button(
            label="← 前へ",
            style=discord.ButtonStyle.secondary,
            disabled=(self._idx == 0),
        )
        prev_btn.callback = self._on_prev

        next_btn = discord.ui.Button(
            label="次へ →",
            style=discord.ButtonStyle.secondary,
            disabled=(self._idx == total - 1),
        )
        next_btn.callback = self._on_next

        remove_btn = discord.ui.Button(
            label="🗑️ 取消",
            style=discord.ButtonStyle.danger,
            disabled=(is_own or current_vote is None),
        )
        remove_btn.callback = self._on_remove

        done_btn = discord.ui.Button(
            label="✅ 完了",
            style=discord.ButtonStyle.success,
        )
        done_btn.callback = self._on_done

        overall_btn = discord.ui.Button(
            label="📝 総評",
            style=discord.ButtonStyle.primary,
        )
        overall_btn.callback = self._on_overall

        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(remove_btn)
        self.add_item(done_btn)
        self.add_item(overall_btn)

    # ------------------------------------------------------------------

    async def _navigate(self, interaction: discord.Interaction, new_idx: int) -> None:
        assert interaction.guild is not None
        async with get_session() as session:
            votes = await vote_repo.get_votes_by_voter(
                session, self._kukai.id, interaction.user.id
            )
        self._votes = {v.submission_id: v for v in votes}
        self._idx = new_idx
        self._build()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        await self._navigate(interaction, self._idx - 1)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        await self._navigate(interaction, self._idx + 1)

    async def _on_remove(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        try:
            ps = self._pub_subs[self._idx]
            async with get_session() as session:
                kukai = await kukai_service.get_kukai(
                    session, self._kukai.id, interaction.guild.id
                )
                await vote_service.remove_vote(
                    session, kukai, interaction.user.id, ps.submission_id
                )
                _, _, self._votes = await load_vote_data(
                    session, self._kukai.id, interaction.user.id
                )
            self._build()
            await interaction.edit_original_response(embed=self.build_embed(), view=self)
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)

    async def _on_done(self, interaction: discord.Interaction) -> None:
        label_map = {lbl.id: lbl for lbl in self._labels}
        voted = [
            (self._votes[ps.submission_id], ps)
            for ps in self._pub_subs
            if ps.submission_id in self._votes
        ]
        if not voted:
            desc = "まだ選句していません。"
        else:
            lines = []
            for vote, ps in voted:
                lbl = label_map.get(vote.vote_label_id)
                lbl_name = lbl.label if lbl else "?"
                comment_part = ""
                if vote.comment:
                    comment_part = f" — {discord_safe(vote.comment.comment[:30])}"
                lines.append(f"No.{ps.number} **{lbl_name}**{comment_part}")
            desc = "\n".join(lines)

        embed = discord.Embed(
            title=f"選句一覧 — {self._kukai.title}",
            description=desc,
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"合計 {len(voted)} 票")
        await interaction.response.edit_message(embed=embed, view=None)

    async def _on_overall(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            oc = await vote_repo.get_overall_comment(
                session, self._kukai.id, interaction.user.id
            )
            current_text = oc.comment if oc else ""

        await interaction.response.send_modal(
            OverallCommentModal(self._kukai.id, interaction.user.id, current_text)
        )
