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
from bot.utils.embed_builder import error_embed, success_embed

_DISCORD_MAX_BYTES = 25 * 1024 * 1024


async def _send_pdf(
    interaction: discord.Interaction,
    pdf_bytes: bytes,
    filename: str,
    kukai_id: int,
) -> None:
    if len(pdf_bytes) <= _DISCORD_MAX_BYTES:
        await interaction.followup.send(
            file=discord.File(io.BytesIO(pdf_bytes), filename=filename),
            ephemeral=True,
        )
    else:
        url = await pdf_service.publish_temp(pdf_bytes, filename, kukai_id)
        await interaction.followup.send(
            content=f"PDFサイズが大きいため一時URLで提供します:\n{url}",
            ephemeral=True,
        )


class PdfCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    pdf = app_commands.Group(name="pdf", description="PDF生成（管理者限定）")

    @pdf.command(name="submission", description="投句一覧PDFを生成します（publish後から利用可）")
    @app_commands.describe(
        kukai_id="句会ID（省略時はチャンネルから自動解決）",
        show_author="俳号を表示するか（デフォルト: True）",
        theme="テーマ名（デフォルト: default）",
    )
    async def pdf_submission(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
        show_author: bool = True,
        theme: str = "default",
    ) -> None:
        if not pdf_service.is_available():
            await interaction.response.send_message(
                embed=error_embed("この環境ではPDF生成が有効化されていません（LUALATEX_BIN未設定）。"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            async with get_session() as session:
                assert interaction.guild is not None
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=interaction.channel_id,
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(
                    session, kukai, interaction.user
                ):
                    await interaction.followup.send(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return

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

        await _send_pdf(
            interaction,
            pdf_bytes,
            filename=f"投句一覧_{kid}.pdf",
            kukai_id=kid,
        )

    @pdf.command(name="result", description="結果PDFを生成します（選句締切後から利用可）")
    @app_commands.describe(
        kukai_id="句会ID（省略時はチャンネルから自動解決）",
        show_author="作者名を表示するか（デフォルト: True）",
        theme="テーマ名（デフォルト: default）",
    )
    async def pdf_result(
        self,
        interaction: discord.Interaction,
        kukai_id: int | None = None,
        show_author: bool = True,
        theme: str = "default",
    ) -> None:
        if not pdf_service.is_available():
            await interaction.response.send_message(
                embed=error_embed("この環境ではPDF生成が有効化されていません（LUALATEX_BIN未設定）。"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            async with get_session() as session:
                assert interaction.guild is not None
                kukai = await kukai_service.resolve_kukai_in_channel(
                    session,
                    guild_id=interaction.guild.id,
                    channel_id=interaction.channel_id,
                    kukai_id=kukai_id,
                )
                if not await permission_service.is_kukai_admin(
                    session, kukai, interaction.user
                ):
                    await interaction.followup.send(
                        embed=error_embed("この操作は句会管理者のみ実行できます。"),
                        ephemeral=True,
                    )
                    return

                pdf_bytes = await pdf_service.build_result_pdf(
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

        await _send_pdf(
            interaction,
            pdf_bytes,
            filename=f"結果_{kid}.pdf",
            kukai_id=kid,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PdfCog(bot))
