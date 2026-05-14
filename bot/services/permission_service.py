"""Permission checks: kukai creation rights and kukai admin rights."""

import json

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.guild_settings import GuildSettings
from bot.repositories import kukai_repo


async def can_create_kukai(
    session: AsyncSession, guild_id: int, member: discord.Member
) -> bool:
    settings = await session.get(GuildSettings, guild_id)
    if settings is None:
        return True  # default: everyone

    role = settings.create_role
    if role == "everyone":
        return True
    if role == "owner":
        return member.id == member.guild.owner_id
    if role == "admin":
        return member.guild_permissions.administrator
    if role == "role":
        allowed: list[int] = json.loads(settings.create_role_ids)
        return any(r.id in allowed for r in member.roles)
    if role == "specific":
        allowed = json.loads(settings.create_user_ids)
        return member.id in allowed
    return False


async def is_kukai_admin(
    session: AsyncSession, kukai, member: discord.Member
) -> bool:
    """True if the member has admin rights over this kukai."""
    # Guild owner and Discord server admins always have access
    if member.id == member.guild.owner_id:
        return True
    if member.guild_permissions.administrator:
        return True
    # Kukai creator
    if kukai.created_by == member.id:
        return True
    # Explicitly added admin
    return await kukai_repo.is_admin(session, kukai.id, member.id)
