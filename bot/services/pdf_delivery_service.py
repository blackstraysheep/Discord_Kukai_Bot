"""Shared PDF visibility policy, generation, and public delivery helpers."""

from __future__ import annotations

import io
from typing import Literal

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from bot.repositories import submission_repo
from bot.services import pdf_service
from bot.services.pdf_service import PdfError
from bot.state_machine.states import KukaiState

PdfKind = Literal["submission", "result"]
DISCORD_MAX_BYTES = 25 * 1024 * 1024
AUTHOR_VISIBLE_STATES = {KukaiState.RESULTS, KukaiState.ENDED}
PUBLIC_RESULT_STATES = {KukaiState.RESULTS, KukaiState.ENDED}


def result_pdf_requires_admin(state: KukaiState) -> bool:
    return state not in PUBLIC_RESULT_STATES


def can_show_author(kukai, requested: bool, *, state: KukaiState | None = None) -> bool:
    if not requested or not bool(getattr(kukai, "author_reveal", False)):
        return False
    return state is None or state in AUTHOR_VISIBLE_STATES


def show_author_request_error(
    kukai,
    requested: bool,
    *,
    state: KukaiState | None = None,
) -> str | None:
    if not requested:
        return None
    if not bool(getattr(kukai, "author_reveal", False)):
        return "この句会は作者非公開に設定されているため、show_author:true は指定できません。"
    if state is not None and state not in AUTHOR_VISIBLE_STATES:
        return "結果公開前は作者名を表示できないため、show_author:true は指定できません。"
    return None


async def has_published_submissions(session: AsyncSession, kukai_id: int) -> bool:
    return bool(await submission_repo.list_published(session, kukai_id))


async def build_pdf(
    session: AsyncSession,
    kukai,
    guild: discord.Guild,
    *,
    kind: PdfKind,
    show_author: bool,
    show_reviewer: bool = True,
    theme: str = "default",
) -> tuple[bytes, str]:
    state = KukaiState.from_value(kukai.state)
    author_error = show_author_request_error(kukai, show_author, state=state)
    if author_error:
        raise PdfError(author_error)
    show_author = can_show_author(kukai, show_author, state=state)

    if kind == "submission":
        pdf_bytes = await pdf_service.build_submission_pdf(
            session,
            kukai,
            guild,
            show_author=show_author,
            theme=theme,
        )
    else:
        pdf_bytes = await pdf_service.build_result_pdf(
            session,
            kukai,
            guild,
            show_author=show_author,
            show_reviewer=show_reviewer,
            theme=theme,
        )
    label = "named" if show_author else "anonymous"
    return pdf_bytes, f"{kind}_{kukai.id}_{label}.pdf"


async def send_pdf_to_channel(
    channel,
    *,
    pdf_bytes: bytes,
    filename: str,
    kukai_id: int,
) -> None:
    if len(pdf_bytes) <= DISCORD_MAX_BYTES:
        await channel.send(file=discord.File(io.BytesIO(pdf_bytes), filename=filename))
        return
    url = await pdf_service.publish_temp(pdf_bytes, filename, kukai_id)
    await channel.send(content=f"PDFサイズが大きいため一時URLで提供します:\n{url}")
