"""Register Discord persistent views for public kukai entry points."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.cogs.kukai_cog import StageActionView
from bot.cogs.result_cog import ResultOpenView
from bot.database import get_session
from bot.models.kukai import Kukai
from bot.state_machine.states import KukaiState

logger = logging.getLogger(__name__)

_STAGE_BUTTON_STATES = (
    KukaiState.ENTRY_OPEN,
    KukaiState.SUBMISSION_OPEN,
    KukaiState.SELECTING_OPEN,
)
_RESULT_FORMATS = (None, "score", "number", "author")
_PERSISTENT_VIEW_KUKAI_STATES = {
    KukaiState.DRAFT.value,
    KukaiState.ENTRY_OPEN.value,
    KukaiState.ENTRY_CLOSED.value,
    KukaiState.SUBMISSION_OPEN.value,
    KukaiState.SUBMISSION_CLOSED.value,
    KukaiState.WAITING_PUBLISH.value,
    KukaiState.SELECTING_OPEN.value,
    KukaiState.SELECTING_CLOSED.value,
    KukaiState.WAITING_RESULTS.value,
    KukaiState.RESULTS.value,
    KukaiState.ENDED.value,
    KukaiState.PAUSED.value,
}


async def register_persistent_views(
    bot: commands.Bot,
    *,
    session: AsyncSession | None = None,
) -> int:
    """Register persistent public-entry views for kukais that may have live messages."""
    if session is None:
        async with get_session() as owned_session:
            kukais = await _list_kukais_for_persistent_views(owned_session)
    else:
        kukais = await _list_kukais_for_persistent_views(session)

    count = _register_views_for_kukais(bot, kukais)
    logger.info("Registered persistent kukai views: %d", count)
    return count


async def _list_kukais_for_persistent_views(session: AsyncSession) -> list[Kukai]:
    result = await session.execute(
        select(Kukai).where(
            Kukai.channel_id.is_not(None),
            Kukai.state.in_(_PERSISTENT_VIEW_KUKAI_STATES),
        )
    )
    return list(result.scalars().all())


def _register_views_for_kukais(bot: commands.Bot, kukais: Iterable[Kukai]) -> int:
    count = 0
    for kukai in kukais:
        for state in _STAGE_BUTTON_STATES:
            bot.add_view(StageActionView(kukai.id, state))
            count += 1

        if kukai.state in {
            KukaiState.RESULTS.value,
            KukaiState.ENDED.value,
        } or kukai.result_message_id is not None:
            for initial_format in _RESULT_FORMATS:
                bot.add_view(ResultOpenView(kukai.id, initial_format=initial_format))
                count += 1
    return count
