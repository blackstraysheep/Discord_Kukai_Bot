from datetime import datetime, timedelta, timezone

import discord
import pytest

from bot.models.entry import Entry
from bot.models.kukai import KukaiAdmin
from bot.services import channel_visibility_service, kukai_service


def _utc(days_from_now: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days_from_now)


class FakeTarget:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.permissions: dict[int, discord.PermissionOverwrite | None] = {}
        self.permission_order: list[int] = []

    def overwrites_for(self, target) -> discord.PermissionOverwrite:
        current = self.permissions.get(target.id)
        if current is None:
            return discord.PermissionOverwrite()
        return current

    async def set_permissions(self, target, *, overwrite=None) -> None:
        self.permission_order.append(target.id)
        self.permissions[target.id] = overwrite


class FakeGuild:
    def __init__(self, channel: FakeChannel, member_ids: list[int]) -> None:
        self.default_role = FakeTarget(0)
        self.me = FakeTarget(999)
        self._channel = channel
        self._members = {member_id: FakeTarget(member_id) for member_id in member_ids}

    def get_channel(self, channel_id: int):
        return self._channel if channel_id == self._channel.id else None

    def get_member(self, user_id: int):
        return self._members.get(user_id)


@pytest.mark.asyncio
async def test_should_restrict_when_manual_state_passes_entry_open(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="手動締切",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        channel_visibility_policy="public_until_participation_close",
    )
    kukai.state = "submission_open"

    assert channel_visibility_service.should_restrict_channel(kukai)


@pytest.mark.asyncio
async def test_public_policy_sync_is_noop(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="公開句会",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    channel = FakeChannel(200)
    guild = FakeGuild(channel, [100])

    result = await channel_visibility_service.sync_channel_permissions(db_session, guild, kukai)

    assert result.ok
    assert result.granted_count == 0
    assert channel.permissions == {}


@pytest.mark.asyncio
async def test_sync_restricts_channel_to_approved_entries_creator_and_admin(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="限定句会",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        channel_visibility_policy="public_until_participation_close",
    )
    kukai.entry_close_at = _utc(-1)
    db_session.add(Entry(kukai_id=kukai.id, user_id=101, haigo="承認", status="approved"))
    db_session.add(Entry(kukai_id=kukai.id, user_id=102, haigo="待ち", status="pending"))
    db_session.add(KukaiAdmin(kukai_id=kukai.id, user_id=103, added_by=100))
    await db_session.flush()
    channel = FakeChannel(200)
    guild = FakeGuild(channel, [100, 101, 102, 103])

    result = await channel_visibility_service.sync_channel_permissions(db_session, guild, kukai)

    assert result.ok
    assert channel.permissions[0].view_channel is False
    assert channel.permissions[999].view_channel is True
    assert channel.permissions[100].view_channel is True
    assert channel.permissions[101].view_channel is True
    assert channel.permissions[103].view_channel is True
    assert 102 not in channel.permissions
    assert channel.permission_order.index(999) < channel.permission_order.index(0)


@pytest.mark.asyncio
async def test_revoke_entry_access_keeps_still_allowed_admin(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="管理者兼参加者",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        channel_visibility_policy="public_until_participation_close",
    )
    kukai.entry_close_at = _utc(-1)
    db_session.add(KukaiAdmin(kukai_id=kukai.id, user_id=101, added_by=100))
    await db_session.flush()
    channel = FakeChannel(200)
    guild = FakeGuild(channel, [100, 101])

    result = await channel_visibility_service.revoke_entry_access(db_session, guild, kukai, 101)

    assert result.ok
    assert result.skipped_count == 1
    assert channel.permissions == {}


@pytest.mark.asyncio
async def test_revoke_entry_access_removes_disallowed_user(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="取消",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        channel_visibility_policy="public_until_participation_close",
    )
    kukai.entry_close_at = _utc(-1)
    channel = FakeChannel(200)
    member = FakeTarget(101)
    channel.permissions[101] = discord.PermissionOverwrite(view_channel=True)
    guild = FakeGuild(channel, [100, 101])
    guild._members[101] = member

    result = await channel_visibility_service.revoke_entry_access(db_session, guild, kukai, 101)

    assert result.ok
    assert result.revoked_count == 1
    assert channel.permissions[101] is None
