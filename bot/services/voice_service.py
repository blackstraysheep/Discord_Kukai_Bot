"""Voice session settings for kukai events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from bot.models.voice_session import VoiceSession
from bot.services.errors import ValidationError

logger = logging.getLogger(__name__)


async def upsert_voice_session(
    session: AsyncSession,
    kukai,
    *,
    vc_channel_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> VoiceSession:
    if end_at is not None and start_at is not None and end_at <= start_at:
        raise ValidationError("ボイス句会の終了時刻は開始時刻より後にしてください。")

    result = await session.execute(
        select(VoiceSession).where(VoiceSession.kukai_id == kukai.id)
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        existing = VoiceSession(
            kukai_id=kukai.id,
            vc_channel_id=vc_channel_id,
            start_at=start_at,
            end_at=end_at,
        )
        session.add(existing)
    else:
        existing.vc_channel_id = vc_channel_id
        existing.start_at = start_at
        existing.end_at = end_at
    await session.flush()
    kukai.__dict__["voice_session"] = existing
    return existing


async def delete_voice_session(session: AsyncSession, kukai) -> None:
    result = await session.execute(
        select(VoiceSession).where(VoiceSession.kukai_id == kukai.id)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    kukai.__dict__["voice_session"] = None


# ── Discord Scheduled Event helpers ──────────────────────────────────────────

def _to_aware_utc(dt: datetime) -> datetime:
    """Convert UTC-naive datetime (as stored in DB) to UTC-aware datetime."""
    return dt.replace(tzinfo=timezone.utc)


async def create_discord_event(
    guild: discord.Guild,
    kukai,
    voice_session: VoiceSession,
) -> int | None:
    """Create a Discord Guild Scheduled Event for the voice session.

    Returns the event ID, or None on failure.
    """
    if voice_session.start_at is None:
        return None
    vc = guild.get_channel(voice_session.vc_channel_id)
    if not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
        logger.warning("create_discord_event: vc_channel_id=%d not found", voice_session.vc_channel_id)
        return None

    entity_type = (
        discord.EntityType.stage_instance
        if isinstance(vc, discord.StageChannel)
        else discord.EntityType.voice
    )
    try:
        event = await guild.create_scheduled_event(
            name=kukai.title,
            start_time=_to_aware_utc(voice_session.start_at),
            end_time=_to_aware_utc(voice_session.end_at) if voice_session.end_at else None,
            entity_type=entity_type,
            channel=vc,
            privacy_level=discord.PrivacyLevel.guild_only,
            description=kukai.description or "",
            reason="句会ボイスセッション",
        )
        return event.id
    except discord.HTTPException:
        logger.exception("Failed to create Discord scheduled event for kukai=%d", kukai.id)
        return None


async def update_discord_event(
    guild: discord.Guild,
    voice_session: VoiceSession,
    *,
    title: str | None = None,
    description: str | None = None,
) -> None:
    """Update an existing Discord Guild Scheduled Event."""
    if voice_session.discord_event_id is None:
        return
    if voice_session.start_at is None:
        return
    vc = guild.get_channel(voice_session.vc_channel_id)
    if not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
        return
    try:
        event = guild.get_scheduled_event(voice_session.discord_event_id)
        if event is None:
            event = await guild.fetch_scheduled_event(voice_session.discord_event_id)
        kwargs: dict = {
            "start_time": _to_aware_utc(voice_session.start_at),
        }
        if voice_session.end_at:
            kwargs["end_time"] = _to_aware_utc(voice_session.end_at)
        if title is not None:
            kwargs["name"] = title
        if description is not None:
            kwargs["description"] = description
        await event.edit(**kwargs)
    except discord.HTTPException:
        logger.exception("Failed to update Discord scheduled event id=%d", voice_session.discord_event_id)


async def delete_discord_event(
    guild: discord.Guild,
    discord_event_id: int,
) -> None:
    """Delete a Discord Guild Scheduled Event."""
    try:
        event = guild.get_scheduled_event(discord_event_id)
        if event is None:
            event = await guild.fetch_scheduled_event(discord_event_id)
        await event.delete()
    except discord.NotFound:
        pass
    except discord.HTTPException:
        logger.exception("Failed to delete Discord scheduled event id=%d", discord_event_id)
