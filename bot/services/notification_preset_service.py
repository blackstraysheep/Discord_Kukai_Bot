"""Notification preset CRUD service."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.notification_preset import NotificationPreset
from bot.repositories import notification_preset_repo
from bot.services.errors import NotFoundError, ValidationError


def entries_to_json(entries: list[dict]) -> str:
    return json.dumps(entries, ensure_ascii=False)


def entries_from_json(json_str: str) -> list[dict]:
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return []


async def list_presets(session: AsyncSession, guild_id: int) -> list[NotificationPreset]:
    return await notification_preset_repo.get_by_guild(session, guild_id)


async def get_default_entries(session: AsyncSession, guild_id: int) -> list[dict] | None:
    preset = await notification_preset_repo.get_default(session, guild_id)
    if preset is None:
        return None
    return entries_from_json(preset.entries_json)


async def create_preset(
    session: AsyncSession,
    guild_id: int,
    created_by: int,
    name: str,
    entries: list[dict],
    *,
    set_default: bool = False,
) -> NotificationPreset:
    if not name.strip():
        raise ValidationError("プリセット名は空にできません。")
    if not entries:
        raise ValidationError("通知エントリーを1件以上指定してください。")

    existing = await notification_preset_repo.get_by_name(session, guild_id, name)
    if existing is not None:
        existing.entries_json = entries_to_json(entries)
        existing.created_by = created_by
        if set_default:
            await notification_preset_repo.set_default(session, guild_id, existing.id)
        await session.flush()
        return existing

    preset = NotificationPreset(
        guild_id=guild_id,
        name=name,
        created_by=created_by,
        entries_json=entries_to_json(entries),
        is_default=False,
    )
    session.add(preset)
    await session.flush()

    if set_default:
        await notification_preset_repo.set_default(session, guild_id, preset.id)
        await session.flush()

    return preset


async def delete_preset(session: AsyncSession, guild_id: int, name: str) -> None:
    preset = await notification_preset_repo.get_by_name(session, guild_id, name)
    if preset is None:
        raise NotFoundError(f"プリセット「{name}」が見つかりません。")
    await session.delete(preset)


async def set_default_preset(session: AsyncSession, guild_id: int, name: str) -> NotificationPreset:
    preset = await notification_preset_repo.get_by_name(session, guild_id, name)
    if preset is None:
        raise NotFoundError(f"プリセット「{name}」が見つかりません。")
    await notification_preset_repo.set_default(session, guild_id, preset.id)
    await session.flush()
    return preset
