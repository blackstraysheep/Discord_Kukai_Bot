"""Voice session settings for kukai events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from bot.models.voice_session import VoiceSession
from bot.services.errors import ValidationError


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
