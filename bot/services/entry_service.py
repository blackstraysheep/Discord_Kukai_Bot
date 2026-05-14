"""Entry (エントリー) lifecycle operations."""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.entry import Entry
from bot.repositories import entry_repo
from bot.services.errors import (
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from bot.state_machine.states import KukaiState

_APPROVAL_ALLOWED = {KukaiState.ENTRY_OPEN, KukaiState.ENTRY_CLOSED}


async def enter(
    session: AsyncSession,
    kukai,
    user_id: int,
    haigo: str | None = None,
) -> Entry:
    """Register a user for a kukai."""
    if not kukai.entry_enabled:
        raise InvalidStateError("この句会はエントリー制ではありません。")
    if KukaiState.from_value(kukai.state) != KukaiState.ENTRY_OPEN:
        raise InvalidStateError("現在エントリーを受け付けていません。")

    existing = await entry_repo.get_by_user(session, kukai.id, user_id)
    if existing:
        if existing.status in ("pending", "approved"):
            raise ValidationError("この句会にはすでに参加しています。")
        if haigo:
            conflict = await entry_repo.has_haigo_conflict(
                session, kukai.id, haigo, exclude_user_id=user_id
            )
            if conflict:
                raise ValidationError("その俳号はこの句会ですでに使われています。別の俳号を指定してください。")
        # rejected or withdrawn → reuse the row
        existing.haigo = haigo or None
        existing.status = "pending" if kukai.entry_approval else "approved"
        existing.approved_by = None
        existing.approved_at = None
        return existing

    if haigo:
        conflict = await entry_repo.has_haigo_conflict(session, kukai.id, haigo)
        if conflict:
            raise ValidationError("その俳号はこの句会ですでに使われています。別の俳号を指定してください。")

    entry = Entry(
        kukai_id=kukai.id,
        user_id=user_id,
        haigo=haigo or None,
        status="pending" if kukai.entry_approval else "approved",
    )
    session.add(entry)
    await session.flush()
    return entry


async def withdraw(session: AsyncSession, kukai, user_id: int) -> Entry:
    """Cancel own entry (only during entry_open)."""
    if KukaiState.from_value(kukai.state) != KukaiState.ENTRY_OPEN:
        raise InvalidStateError("エントリーの取消は受付期間中のみ可能です。")

    entry = await entry_repo.get_by_user(session, kukai.id, user_id)
    if not entry or entry.status in ("rejected", "withdrawn"):
        raise NotFoundError("有効なエントリーが見つかりません。")

    entry.status = "withdrawn"
    return entry


async def approve(
    session: AsyncSession,
    kukai,
    approver_id: int,
    target_user_id: int,
) -> Entry:
    """Admin: approve a pending entry."""
    if not kukai.entry_approval:
        raise ValidationError("この句会は承認制ではありません。")
    if KukaiState.from_value(kukai.state) not in _APPROVAL_ALLOWED:
        raise InvalidStateError("エントリー管理はエントリー期間中または締切後のみ可能です。")

    entry = await entry_repo.get_by_user(session, kukai.id, target_user_id)
    if not entry or entry.status == "withdrawn":
        raise NotFoundError("エントリーが見つかりません。")

    entry.status = "approved"
    entry.approved_by = approver_id
    entry.approved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return entry


async def reject(
    session: AsyncSession,
    kukai,
    rejecter_id: int,
    target_user_id: int,
) -> Entry:
    """Admin: reject a pending (or approved) entry."""
    if KukaiState.from_value(kukai.state) not in _APPROVAL_ALLOWED:
        raise InvalidStateError("エントリー管理はエントリー期間中または締切後のみ可能です。")

    entry = await entry_repo.get_by_user(session, kukai.id, target_user_id)
    if not entry or entry.status == "withdrawn":
        raise NotFoundError("エントリーが見つかりません。")

    entry.status = "rejected"
    return entry


async def admin_remove(
    session: AsyncSession,
    kukai,
    target_user_id: int,
) -> None:
    """Admin: hard-delete an entry after entry_closed."""
    state = KukaiState.from_value(kukai.state)
    if state == KukaiState.ENTRY_OPEN:
        raise InvalidStateError(
            "受付期間中は管理者削除できません。"
            "ユーザー自身に取消させるか、締切後に操作してください。"
        )
    if state in KukaiState.terminal_states():
        raise InvalidStateError("終了済みの句会では操作できません。")

    entry = await entry_repo.get_by_user(session, kukai.id, target_user_id)
    if not entry:
        raise NotFoundError("エントリーが見つかりません。")

    await session.delete(entry)


async def list_entries(
    session: AsyncSession,
    kukai_id: int,
    status: str | None = None,
) -> list[Entry]:
    return await entry_repo.list_by_kukai(session, kukai_id, status)
