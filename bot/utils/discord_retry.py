"""Discord message send retry helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import discord

_T = TypeVar("_T")
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


async def send_with_retry(
    sender: Callable[[], Awaitable[_T]],
    *,
    retries: int = 2,
    base_delay: float = 0.6,
) -> _T:
    """Run Discord send call with bounded retries for transient HTTP errors."""
    attempt = 0
    while True:
        try:
            return await sender()
        except discord.Forbidden:
            raise
        except discord.HTTPException as exc:
            status = getattr(exc, "status", None)
            if status not in _RETRYABLE_HTTP_STATUS or attempt >= retries:
                raise
            await asyncio.sleep(base_delay * (attempt + 1))
            attempt += 1
