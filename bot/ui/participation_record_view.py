"""Ephemeral participation-record options opened from the server portal."""

from __future__ import annotations

import discord

from bot.cogs.record_cog import send_participation_record
from bot.services.errors import ServiceError
from bot.utils.embed_builder import COLOR_INFO, error_embed


class HaigoFilterModal(discord.ui.Modal, title="俳号フィルター"):
    haigo = discord.ui.TextInput(
        label="俳号（完全一致・空欄で解除）",
        required=False,
        max_length=100,
    )

    def __init__(self, owner: "ParticipationRecordOptionsView") -> None:
        super().__init__()
        self.owner = owner
        self.haigo.default = owner.haigo or ""

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner.user_id:
            await interaction.response.send_message(
                embed=error_embed("この画面は開いた本人だけが操作できます。"),
                ephemeral=True,
            )
            return
        value = str(self.haigo).strip()
        self.owner.haigo = value or None
        await interaction.response.send_message(
            f"俳号フィルターを{'「' + value + '」に設定しました' if value else '解除しました'}。",
            ephemeral=True,
        )


class ParticipationRecordOptionsView(discord.ui.View):
    def __init__(
        self,
        *,
        bot: discord.Client,
        guild: discord.Guild,
        user: discord.Member | discord.User,
        allow_other: bool,
    ) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.user_id = user.id
        self.target: discord.Member | discord.User = user
        self.allow_other = allow_other
        self.scope = "current"
        self.group_by = "kukai"
        self.limit = 5
        self.haigo: str | None = None
        self._rebuild()

    @property
    def is_self(self) -> bool:
        return self.target.id == self.user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            embed=error_embed("この画面は開いた本人だけが操作できます。"),
            ephemeral=True,
        )
        return False

    def build_embed(self) -> discord.Embed:
        target_name = getattr(self.target, "display_name", self.target.name)
        scope_label = "全サーバー" if self.scope == "all" else "現在のサーバー"
        group_labels = {"kukai": "句会別", "server": "サーバー別", "haigo": "俳号別"}
        description = (
            f"対象: **{target_name}**\n"
            f"範囲: **{scope_label}**\n"
            f"集計: **{group_labels[self.group_by]}**\n"
            f"要約件数: **{self.limit}件**\n"
            f"俳号: **{self.haigo or '指定なし'}**"
        )
        if not self.allow_other:
            description += "\n\nこのサーバーでは他参加者の記録は非公開です。"
        return discord.Embed(title="参加の記録", description=description, color=COLOR_INFO)

    def _rebuild(self) -> None:
        self.clear_items()
        if self.allow_other:
            target_select = discord.ui.UserSelect(
                placeholder=f"対象: {getattr(self.target, 'display_name', self.target.name)}",
                min_values=1,
                max_values=1,
                row=0,
            )

            async def target_callback(interaction: discord.Interaction) -> None:
                target = target_select.values[0]
                if (
                    not isinstance(target, discord.Member)
                    or target.guild.id != self.guild.id
                    or target.bot
                ):
                    await interaction.response.send_message(
                        embed=error_embed("同じサーバーにいるBot以外のユーザーを選んでください。"),
                        ephemeral=True,
                    )
                    return
                self.target = target
                if not self.is_self:
                    self.scope = "current"
                    if self.group_by == "server":
                        self.group_by = "kukai"
                self._rebuild()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            target_select.callback = target_callback
            self.add_item(target_select)

        scope_options = [
            discord.SelectOption(label="現在のサーバー", value="current", default=self.scope == "current")
        ]
        if self.is_self:
            scope_options.append(
                discord.SelectOption(label="全サーバー", value="all", default=self.scope == "all")
            )
        scope_select = discord.ui.Select(
            placeholder="表示範囲",
            options=scope_options,
            min_values=1,
            max_values=1,
            row=1,
        )

        async def scope_callback(interaction: discord.Interaction) -> None:
            self.scope = scope_select.values[0]
            self._rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        scope_select.callback = scope_callback
        self.add_item(scope_select)

        group_options = [
            discord.SelectOption(label="句会別", value="kukai", default=self.group_by == "kukai"),
            discord.SelectOption(label="俳号別", value="haigo", default=self.group_by == "haigo"),
        ]
        if self.is_self:
            group_options.insert(
                1,
                discord.SelectOption(
                    label="サーバー別", value="server", default=self.group_by == "server"
                ),
            )
        group_select = discord.ui.Select(
            placeholder="表示軸",
            options=group_options,
            min_values=1,
            max_values=1,
            row=2,
        )

        async def group_callback(interaction: discord.Interaction) -> None:
            self.group_by = group_select.values[0]
            self._rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        group_select.callback = group_callback
        self.add_item(group_select)

        limit_select = discord.ui.Select(
            placeholder="Discord上の要約件数",
            options=[
                discord.SelectOption(label=f"{value}件", value=str(value), default=self.limit == value)
                for value in range(1, 26)
            ],
            min_values=1,
            max_values=1,
            row=3,
        )

        async def limit_callback(interaction: discord.Interaction) -> None:
            self.limit = int(limit_select.values[0])
            self._rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        limit_select.callback = limit_callback
        self.add_item(limit_select)

        haigo_button = discord.ui.Button(
            label="俳号を指定" if self.haigo is None else "俳号を変更",
            style=discord.ButtonStyle.secondary,
            row=4,
        )

        async def haigo_callback(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(HaigoFilterModal(self))

        haigo_button.callback = haigo_callback
        self.add_item(haigo_button)

        show_button = discord.ui.Button(label="記録を表示", style=discord.ButtonStyle.primary, row=4)

        async def show_callback(interaction: discord.Interaction) -> None:
            if (
                not isinstance(self.target, discord.Member)
                or self.target.guild.id != self.guild.id
                or self.target.bot
            ):
                await interaction.response.send_message(
                    embed=error_embed("対象ユーザーがこのサーバーに見つかりません。"),
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            try:
                await send_participation_record(
                    interaction,
                    bot=self.bot,
                    target=self.target,
                    scope=self.scope,  # type: ignore[arg-type]
                    group_by=self.group_by,  # type: ignore[arg-type]
                    haigo=self.haigo,
                    limit=self.limit,
                )
            except ServiceError as error:
                await interaction.followup.send(embed=error_embed(str(error)), ephemeral=True)

        show_button.callback = show_callback
        self.add_item(show_button)
