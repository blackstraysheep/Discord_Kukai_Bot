"""PDF generation commands: /pdf submission, /pdf result"""

from __future__ import annotations

import io

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_session
from bot.services import kukai_service, pdf_service, permission_service
from bot.services.errors import ServiceError
from bot.services.pdf_service import PdfError
from bot.state_machine.states import KukaiState
from bot.utils.embed_builder import error_embed

_DISCORD_MAX_BYTES = 25 * 1024 * 1024

# 結果公開後のみ作者名を出せる
_AUTHOR_VISIBLE_STATES = {KukaiState.RESULTS, KukaiState.ENDED}
_PUBLIC_RESULT_STATES = {KukaiState.RESULTS, KukaiState.ENDED}


def _result_pdf_requires_admin(state: KukaiState) -> bool:
    return state not in _PUBLIC_RESULT_STATES


def _can_show_pdf_author(kukai, requested: bool, *, state: KukaiState | None = None) -> bool:
    if not requested or not bool(getattr(kukai, "author_reveal", False)):
        return False
    if state is not None and state not in _AUTHOR_VISIBLE_STATES:
        return False
    return True


def _can_show_result_author(
    kukai,
    requested: bool,
    *,
    state: KukaiState | None = None,
) -> bool:
    return _can_show_pdf_author(kukai, requested, state=state)


async def _send_pdf(
    interaction: discord.Interaction,
    pdf_bytes: bytes,
    filename: str,
    kukai_id: int,
    *,
    ephemeral: bool,
) -> None:
    if len(pdf_bytes) <= _DISCORD_MAX_BYTES:
        await interaction.followup.send(
            file=discord.File(io.BytesIO(pdf_bytes), filename=filename),
            ephemeral=ephemeral,
        )
    else:
        url = await pdf_service.publish_temp(pdf_bytes, filename, kukai_id)
        await interaction.followup.send(
            content=f"PDFサイズが大きいため一時URLで提供します:\n{url}",
            ephemeral=ephemeral,
        )


class PdfCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    pdf = app_commands.Group(name="pdf", description="PDF生成")

    @pdf.command(name="submission", description="投句一覧PDFを生成します（publish後から利用可）")
    @app_commands.describe(
        kukai_id="句会ID（省略時はチャンネルから自動解決）",
        show_author="作者名を表示するか（結果公開前は強制的に非表示）",
        theme="テーマ名（デフォルト: default）",
        public="チャンネルに投稿するか（管理者のみ・デフォルト: False）",
    )
    async def pdf_submission(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
        show_author: bool = True,
        theme: str = "default",
        public: bool = False,
    ) -> None:
        if not pdf_service.is_available():
            await interaction.response.send_message(
                embed=error_embed("この環境ではPDF生成が有効化されていません（LUALATEX_BIN未設定）。"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=not public)

        try:
            async with get_session() as session:
                assert interaction.guild is not None
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=interaction.channel_id,
                    kukai_id=kukai_id,
                )

                if public and not await permission_service.is_kukai_admin(
                    session, kukai, interaction.user
                ):
                    await interaction.followup.send(
                        embed=error_embed("チャンネルへの投稿は句会管理者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return

                show_author = _can_show_pdf_author(
                    kukai,
                    show_author,
                    state=KukaiState.from_value(kukai.state),
                )

                pdf_bytes = await pdf_service.build_submission_pdf(
                    session,
                    kukai,
                    interaction.guild,
                    show_author=show_author,
                    theme=theme,
                )
                kid = kukai.id

        except PdfError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
            return
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
            return

        label = "named" if show_author else "anonymous"
        await _send_pdf(
            interaction,
            pdf_bytes,
            filename=f"submission_{kid}_{label}.pdf",
            kukai_id=kid,
            ephemeral=not public,
        )

    @pdf.command(name="result", description="結果PDFを生成します（選句締切後から利用可）")
    @app_commands.describe(
        kukai_id="句会ID（省略時はチャンネルから自動解決）",
        show_author="作者名を表示するか（デフォルト: True）",
        show_reviewer="選評者名を表示するか（デフォルト: True）",
        theme="テーマ名（デフォルト: default）",
        public="チャンネルに投稿するか（管理者のみ・デフォルト: False）",
    )
    async def pdf_result(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
        show_author: bool = True,
        show_reviewer: bool = True,
        theme: str = "default",
        public: bool = False,
    ) -> None:
        if not pdf_service.is_available():
            await interaction.response.send_message(
                embed=error_embed("この環境ではPDF生成が有効化されていません（LUALATEX_BIN未設定）。"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=not public)

        try:
            async with get_session() as session:
                assert interaction.guild is not None
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=interaction.channel_id,
                    kukai_id=kukai_id,
                )
                state = KukaiState.from_value(kukai.state)

                if public and not await permission_service.is_kukai_admin(
                    session, kukai, interaction.user
                ):
                    await interaction.followup.send(
                        embed=error_embed("チャンネルへの投稿は句会管理者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return
                if public and state not in _PUBLIC_RESULT_STATES:
                    await interaction.followup.send(
                        embed=error_embed("結果公開前のPDFはチャンネル投稿できません。"),
                        ephemeral=True,
                    )
                    return
                if _result_pdf_requires_admin(state) and not await permission_service.is_kukai_admin(
                    session, kukai, interaction.user
                ):
                    await interaction.followup.send(
                        embed=error_embed("結果公開前のPDF生成は句会管理者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return
                show_author = _can_show_result_author(kukai, show_author, state=state)

                pdf_bytes = await pdf_service.build_result_pdf(
                    session,
                    kukai,
                    interaction.guild,
                    show_author=show_author,
                    show_reviewer=show_reviewer,
                    theme=theme,
                )
                kid = kukai.id

        except PdfError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
            return
        except ServiceError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
            return

        label = "named" if show_author else "anonymous"
        await _send_pdf(
            interaction,
            pdf_bytes,
            filename=f"result_{kid}_{label}.pdf",
            kukai_id=kid,
            ephemeral=not public,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PdfCog(bot))
