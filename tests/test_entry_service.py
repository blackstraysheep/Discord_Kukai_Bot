"""Unit tests for entry_service."""

from datetime import timedelta, timezone, datetime

import pytest

from bot.services import entry_service, kukai_service
from bot.services.errors import InvalidStateError, NotFoundError, ValidationError
from bot.state_machine.states import KukaiState


def _utc(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


async def _make_kukai(session, *, entry_approval=False, entry_enabled=True):
    kukai = await kukai_service.create_kukai(
        session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="テスト句会",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    kukai.entry_approval = entry_approval
    kukai.entry_enabled = entry_enabled
    await session.flush()
    return kukai


@pytest.mark.asyncio
async def test_enter_no_approval(db_session):
    kukai = await _make_kukai(db_session, entry_approval=False)
    await kukai_service.proceed(db_session, kukai)  # draft → entry_open
    await db_session.commit()

    entry = await entry_service.enter(db_session, kukai, user_id=1, haigo="春風")
    assert entry.status == "approved"
    assert entry.haigo == "春風"


@pytest.mark.asyncio
async def test_enter_with_approval(db_session):
    kukai = await _make_kukai(db_session, entry_approval=True)
    await kukai_service.proceed(db_session, kukai)  # → entry_open
    await db_session.commit()

    entry = await entry_service.enter(db_session, kukai, user_id=1)
    assert entry.status == "pending"


@pytest.mark.asyncio
async def test_entry_open_with_past_deadline_is_not_late(db_session):
    """ENTRY_OPEN state: deadline passed in time but state not advanced → not late."""
    kukai = await _make_kukai(db_session, entry_approval=False)
    kukai.entry_close_at = _utc(-1)
    await kukai_service.proceed(db_session, kukai)  # → entry_open
    await db_session.commit()

    entry = await entry_service.enter(db_session, kukai, user_id=1)
    assert entry.status == "approved"


@pytest.mark.asyncio
async def test_late_entry_allowed_in_entry_closed_as_pending(db_session):
    kukai = await _make_kukai(db_session, entry_approval=False)
    await kukai_service.proceed(db_session, kukai)  # → entry_open
    await kukai_service.proceed(db_session, kukai)  # → entry_closed
    await db_session.commit()

    entry = await entry_service.enter(db_session, kukai, user_id=1)
    assert entry.status == "pending"


@pytest.mark.asyncio
async def test_approve_late_pending_without_approval_mode(db_session):
    """ENTRY_CLOSED state creates pending entry; admin can approve even without approval mode."""
    kukai = await _make_kukai(db_session, entry_approval=False)
    await kukai_service.proceed(db_session, kukai)  # → entry_open
    await kukai_service.proceed(db_session, kukai)  # → entry_closed
    await db_session.commit()

    await entry_service.enter(db_session, kukai, user_id=1)
    entry = await entry_service.approve(db_session, kukai, approver_id=100, target_user_id=1)
    assert entry.status == "approved"
    assert entry.approved_by == 100


@pytest.mark.asyncio
async def test_enter_duplicate_raises(db_session):
    kukai = await _make_kukai(db_session)
    await kukai_service.proceed(db_session, kukai)
    await db_session.commit()

    await entry_service.enter(db_session, kukai, user_id=1)
    with pytest.raises(ValidationError):
        await entry_service.enter(db_session, kukai, user_id=1)


@pytest.mark.asyncio
async def test_enter_wrong_state_raises(db_session):
    kukai = await _make_kukai(db_session)
    # still in draft
    with pytest.raises(InvalidStateError):
        await entry_service.enter(db_session, kukai, user_id=1)


@pytest.mark.asyncio
async def test_withdraw(db_session):
    kukai = await _make_kukai(db_session)
    await kukai_service.proceed(db_session, kukai)
    await db_session.commit()

    await entry_service.enter(db_session, kukai, user_id=1)
    entry = await entry_service.withdraw(db_session, kukai, user_id=1)
    assert entry.status == "withdrawn"


@pytest.mark.asyncio
async def test_withdraw_wrong_state_raises(db_session):
    kukai = await _make_kukai(db_session)
    await kukai_service.proceed(db_session, kukai)  # → entry_open
    await entry_service.enter(db_session, kukai, user_id=1)
    await kukai_service.proceed(db_session, kukai)  # → entry_closed
    await db_session.commit()

    with pytest.raises(InvalidStateError):
        await entry_service.withdraw(db_session, kukai, user_id=1)


@pytest.mark.asyncio
async def test_approve_reject(db_session):
    kukai = await _make_kukai(db_session, entry_approval=True)
    await kukai_service.proceed(db_session, kukai)  # → entry_open
    await db_session.commit()

    await entry_service.enter(db_session, kukai, user_id=1)

    entry = await entry_service.approve(db_session, kukai, approver_id=100, target_user_id=1)
    assert entry.status == "approved"
    assert entry.approved_by == 100

    entry = await entry_service.reject(db_session, kukai, rejecter_id=100, target_user_id=1)
    assert entry.status == "rejected"


@pytest.mark.asyncio
async def test_reentry_after_withdraw(db_session):
    kukai = await _make_kukai(db_session)
    await kukai_service.proceed(db_session, kukai)
    await db_session.commit()

    await entry_service.enter(db_session, kukai, user_id=1, haigo="春")
    await entry_service.withdraw(db_session, kukai, user_id=1)
    entry = await entry_service.enter(db_session, kukai, user_id=1, haigo="夏")
    assert entry.status == "approved"
    assert entry.haigo == "夏"


@pytest.mark.asyncio
async def test_admin_remove(db_session):
    kukai = await _make_kukai(db_session)
    await kukai_service.proceed(db_session, kukai)  # → entry_open
    await entry_service.enter(db_session, kukai, user_id=1)
    await kukai_service.proceed(db_session, kukai)  # → entry_closed
    await db_session.commit()

    await entry_service.admin_remove(db_session, kukai, target_user_id=1)
    await db_session.commit()

    from bot.repositories import entry_repo
    assert await entry_repo.get_by_user(db_session, kukai.id, 1) is None


@pytest.mark.asyncio
async def test_admin_remove_blocked_during_entry_open(db_session):
    kukai = await _make_kukai(db_session)
    await kukai_service.proceed(db_session, kukai)  # → entry_open
    await entry_service.enter(db_session, kukai, user_id=1)
    await db_session.commit()

    with pytest.raises(InvalidStateError):
        await entry_service.admin_remove(db_session, kukai, target_user_id=1)


@pytest.mark.asyncio
async def test_count_participants_no_approval(db_session):
    kukai = await _make_kukai(db_session, entry_approval=False)
    await kukai_service.proceed(db_session, kukai)
    await entry_service.enter(db_session, kukai, user_id=1)
    await entry_service.enter(db_session, kukai, user_id=2)
    await db_session.commit()

    from bot.repositories import entry_repo
    count = await entry_repo.count_participants(db_session, kukai.id, approval_mode=False)
    assert count == 2


@pytest.mark.asyncio
async def test_count_participants_with_approval(db_session):
    kukai = await _make_kukai(db_session, entry_approval=True)
    await kukai_service.proceed(db_session, kukai)
    await entry_service.enter(db_session, kukai, user_id=1)
    await entry_service.enter(db_session, kukai, user_id=2)
    await db_session.commit()

    from bot.repositories import entry_repo
    # Both pending → count 0 for approved-only mode
    count = await entry_repo.count_participants(db_session, kukai.id, approval_mode=True)
    assert count == 0

    await entry_service.approve(db_session, kukai, approver_id=100, target_user_id=1)
    await db_session.commit()

    count = await entry_repo.count_participants(db_session, kukai.id, approval_mode=True)
    assert count == 1
