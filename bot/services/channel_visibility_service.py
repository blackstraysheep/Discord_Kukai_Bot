"""Discord channel visibility synchronization for kukai participant-only channels."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.entry import Entry
from bot.models.kukai import KukaiAdmin

logger = logging.getLogger(__name__)

POLICY_PUBLIC = "public"
POLICY_PUBLIC_UNTIL_PARTICIPATION_CLOSE = "public_until_participation_close"


@dataclass
class ChannelVisibilitySyncResult:
    mode: str
    channel_id: int | None
    granted_count: int = 0
    revoked_count: int = 0
    skipped_count: int = 0
    failed_user_ids: list[int] = field(default_factory=list)
    message: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed_user_ids and not self.message.startswith("失敗:")

    def summary(self) -> str:
        base = self.message or "チャンネル権限を同期しました。"
        counts = (
            f"付与/更新: {self.granted_count}件、"
            f"削除: {self.revoked_count}件、"
            f"スキップ: {self.skipped_count}件"
        )
        if self.failed_user_ids:
            counts += f"、失敗: {len(self.failed_user_ids)}件"
        return f"{base}\n{counts}"


def should_restrict_channel(kukai, *, now: datetime | None = None) -> bool:
    if getattr(kukai, "channel_visibility_policy", POLICY_PUBLIC) != POLICY_PUBLIC_UNTIL_PARTICIPATION_CLOSE:
        return False

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    state = str(getattr(kukai, "state", ""))
    if getattr(kukai, "entry_enabled", False) and getattr(kukai, "entry_close_at", None) is not None:
        return kukai.entry_close_at <= now or state not in {"draft", "entry_open"}

    deadline = getattr(kukai, "submission_close_at", None)
    if deadline is not None and deadline <= now:
        return True
    return state in {
        "submission_closed",
        "waiting_publish",
        "selecting_open",
        "selecting_closed",
        "waiting_results",
        "results",
        "ended",
    }


async def apply_initial_channel_visibility(
    guild: discord.Guild,
    kukai,
    channel: discord.TextChannel,
) -> ChannelVisibilitySyncResult:
    del guild
    return ChannelVisibilitySyncResult(
        mode=getattr(kukai, "channel_visibility_policy", POLICY_PUBLIC),
        channel_id=getattr(channel, "id", None),
        message="作成直後は公開状態のままにしました。",
    )


async def restrict_channel_to_participants(
    session: AsyncSession,
    guild: discord.Guild,
    kukai,
    channel: discord.TextChannel | None = None,
) -> ChannelVisibilitySyncResult:
    return await sync_channel_permissions(session, guild, kukai, channel=channel, force=True)


async def sync_channel_permissions(
    session: AsyncSession,
    guild: discord.Guild,
    kukai,
    channel: discord.TextChannel | None = None,
    *,
    force: bool = False,
) -> ChannelVisibilitySyncResult:
    mode = getattr(kukai, "channel_visibility_policy", POLICY_PUBLIC)
    channel_id = getattr(kukai, "channel_id", None)
    result = ChannelVisibilitySyncResult(mode=mode, channel_id=channel_id)

    if mode == POLICY_PUBLIC:
        result.message = "公開チャンネルのため同期は不要です。"
        return result
    if mode != POLICY_PUBLIC_UNTIL_PARTICIPATION_CLOSE:
        result.message = f"失敗: 未対応の閲覧ポリシーです: {mode}"
        return result
    if not force and not should_restrict_channel(kukai):
        result.message = "参加者限定化のタイミング前のため同期は不要です。"
        return result

    channel = channel or _resolve_text_channel(guild, channel_id)
    if channel is None:
        result.message = "失敗: 開催チャンネルが見つかりません。"
        return result

    result.channel_id = getattr(channel, "id", channel_id)
    allowed_user_ids = await _allowed_user_ids(session, kukai)

    try:
        bot_member = getattr(guild, "me", None)
        if bot_member is not None:
            await _set_overwrite(
                channel,
                bot_member,
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                read_message_history=True,
            )

        for user_id in sorted(allowed_user_ids):
            member = guild.get_member(user_id)
            if member is None:
                result.skipped_count += 1
                continue
            try:
                await _set_overwrite(
                    channel,
                    member,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
                result.granted_count += 1
            except (discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "event=channel_visibility_sync_failed kukai_id=%s channel_id=%s user_id=%s",
                    getattr(kukai, "id", None),
                    result.channel_id,
                    user_id,
                )
                result.failed_user_ids.append(user_id)

        everyone = getattr(guild, "default_role", None)
        if everyone is not None:
            await _set_overwrite(channel, everyone, view_channel=False)
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.warning(
            "event=channel_visibility_sync_failed kukai_id=%s channel_id=%s error=%s",
            getattr(kukai, "id", None),
            result.channel_id,
            error,
        )
        result.message = f"失敗: チャンネル権限の同期に失敗しました: {error}"
        return result

    result.message = "参加者限定チャンネルの権限を同期しました。"
    return result


async def grant_entry_access(
    session: AsyncSession,
    guild: discord.Guild,
    kukai,
    entry: Entry,
) -> ChannelVisibilitySyncResult:
    mode = getattr(kukai, "channel_visibility_policy", POLICY_PUBLIC)
    result = ChannelVisibilitySyncResult(mode=mode, channel_id=getattr(kukai, "channel_id", None))
    if mode == POLICY_PUBLIC:
        result.message = "公開チャンネルのため個別付与は不要です。"
        return result
    if not should_restrict_channel(kukai):
        result.message = "参加者限定化のタイミング前のため個別付与は不要です。"
        return result
    if entry.status != "approved":
        result.message = "承認済みエントリーではないため個別付与は不要です。"
        return result

    channel = _resolve_text_channel(guild, getattr(kukai, "channel_id", None))
    member = guild.get_member(entry.user_id)
    if channel is None:
        result.message = "失敗: 開催チャンネルが見つかりません。"
        return result
    if member is None:
        result.skipped_count = 1
        result.message = "対象メンバーが見つからないため個別付与をスキップしました。"
        return result

    try:
        await _set_overwrite(
            channel,
            member,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )
        result.granted_count = 1
        result.message = "参加者にチャンネル閲覧権限を付与しました。"
    except (discord.Forbidden, discord.HTTPException):
        logger.exception(
            "event=channel_visibility_sync_failed kukai_id=%s channel_id=%s user_id=%s",
            getattr(kukai, "id", None),
            getattr(channel, "id", None),
            entry.user_id,
        )
        result.failed_user_ids.append(entry.user_id)
        result.message = "失敗: チャンネル閲覧権限の付与に失敗しました。"
    return result


async def revoke_entry_access(
    session: AsyncSession,
    guild: discord.Guild,
    kukai,
    user_id: int,
) -> ChannelVisibilitySyncResult:
    mode = getattr(kukai, "channel_visibility_policy", POLICY_PUBLIC)
    result = ChannelVisibilitySyncResult(mode=mode, channel_id=getattr(kukai, "channel_id", None))
    if mode == POLICY_PUBLIC:
        result.message = "公開チャンネルのため個別削除は不要です。"
        return result
    if not should_restrict_channel(kukai):
        result.message = "参加者限定化のタイミング前のため個別削除は不要です。"
        return result

    allowed_user_ids = await _allowed_user_ids(session, kukai)
    if user_id in allowed_user_ids:
        result.skipped_count = 1
        result.message = "対象ユーザーはまだ閲覧対象のため削除しません。"
        return result

    channel = _resolve_text_channel(guild, getattr(kukai, "channel_id", None))
    member = guild.get_member(user_id)
    if channel is None:
        result.message = "失敗: 開催チャンネルが見つかりません。"
        return result
    if member is None:
        result.skipped_count = 1
        result.message = "対象メンバーが見つからないため個別削除をスキップしました。"
        return result

    try:
        await channel.set_permissions(member, overwrite=None)
        result.revoked_count = 1
        result.message = "対象ユーザーのチャンネル閲覧権限を削除しました。"
    except (discord.Forbidden, discord.HTTPException):
        logger.exception(
            "event=channel_visibility_sync_failed kukai_id=%s channel_id=%s user_id=%s",
            getattr(kukai, "id", None),
            getattr(channel, "id", None),
            user_id,
        )
        result.failed_user_ids.append(user_id)
        result.message = "失敗: チャンネル閲覧権限の削除に失敗しました。"
    return result


async def _allowed_user_ids(session: AsyncSession, kukai) -> set[int]:
    result = await session.execute(
        select(Entry.user_id).where(Entry.kukai_id == kukai.id, Entry.status == "approved")
    )
    ids = {int(user_id) for user_id in result.scalars().all()}
    ids.add(int(kukai.created_by))

    admin_result = await session.execute(select(KukaiAdmin.user_id).where(KukaiAdmin.kukai_id == kukai.id))
    ids.update(int(user_id) for user_id in admin_result.scalars().all())
    return ids


def _resolve_text_channel(guild: discord.Guild, channel_id: int | None):
    if channel_id is None:
        return None
    channel = guild.get_channel(channel_id)
    if channel is None or not hasattr(channel, "set_permissions"):
        return None
    return channel


async def _set_overwrite(channel, target, **permissions) -> None:
    overwrite = channel.overwrites_for(target)
    for name, value in permissions.items():
        setattr(overwrite, name, value)
    await channel.set_permissions(target, overwrite=overwrite)
