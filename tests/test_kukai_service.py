"""Unit tests for kukai_service and state_machine."""

from datetime import datetime, timedelta, timezone

import pytest

from bot.models.kukai import Kukai
from bot.models.vote_rule import VoteLabel
from bot.services import kukai_service
from bot.services.errors import DeadlineConflictError, InvalidStateError, NotFoundError
from bot.state_machine.states import KukaiState


def _utc(days_from_now: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days_from_now)


@pytest.mark.asyncio
async def test_create_kukai_creates_default_labels(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="テスト句会",
        submission_close_at=_utc(7),
        voting_close_at=_utc(14),
    )
    await db_session.commit()

    from sqlalchemy import select
    labels = (
        await db_session.execute(
            select(VoteLabel).where(VoteLabel.kukai_id == kukai.id)
        )
    ).scalars().all()

    assert len(labels) == 3
    assert {l.label for l in labels} == {"特選", "並選", "予選"}
    points = {l.label: l.point for l in labels}
    assert points["特選"] == 2
    assert points["並選"] == 1
    assert points["予選"] == 0


@pytest.mark.asyncio
async def test_create_kukai_deadline_conflict(db_session):
    with pytest.raises(DeadlineConflictError):
        await kukai_service.create_kukai(
            db_session,
            guild_id=1,
            created_by=100,
            channel_id=200,
            title="締切逆転",
            submission_close_at=_utc(14),
            voting_close_at=_utc(7),  # before submission
        )


@pytest.mark.asyncio
async def test_get_kukai_wrong_guild(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="別ギルド",
        submission_close_at=_utc(7),
        voting_close_at=_utc(14),
    )
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await kukai_service.get_kukai(db_session, kukai.id, guild_id=999)


@pytest.mark.asyncio
async def test_state_machine_proceed(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="状態遷移テスト",
        submission_close_at=_utc(7),
        voting_close_at=_utc(14),
    )
    await db_session.commit()
    assert kukai.state == KukaiState.DRAFT

    # entry_enabled=True by default → draft → entry_open
    new_state = await kukai_service.proceed(db_session, kukai)
    assert new_state == KukaiState.ENTRY_OPEN
    assert kukai.state == KukaiState.ENTRY_OPEN


@pytest.mark.asyncio
async def test_state_machine_proceed_skip_entry(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="エントリースキップ",
        submission_close_at=_utc(7),
        voting_close_at=_utc(14),
    )
    kukai.entry_enabled = False
    await db_session.commit()

    new_state = await kukai_service.proceed(db_session, kukai)
    assert new_state == KukaiState.SUBMISSION_OPEN


@pytest.mark.asyncio
async def test_pause_and_resume(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="ポーズテスト",
        submission_close_at=_utc(7),
        voting_close_at=_utc(14),
    )
    await kukai_service.proceed(db_session, kukai)  # → entry_open
    await db_session.commit()

    await kukai_service.pause(db_session, kukai)
    assert kukai.state == KukaiState.PAUSED
    assert kukai.pre_pause_state == KukaiState.ENTRY_OPEN

    restored = await kukai_service.resume(db_session, kukai)
    assert restored == KukaiState.ENTRY_OPEN
    assert kukai.state == KukaiState.ENTRY_OPEN
    assert kukai.pre_pause_state is None


@pytest.mark.asyncio
async def test_cancel(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="中止テスト",
        submission_close_at=_utc(7),
        voting_close_at=_utc(14),
    )
    await db_session.commit()
    await kukai_service.cancel(db_session, kukai)
    assert kukai.state == KukaiState.CANCELLED


@pytest.mark.asyncio
async def test_cannot_proceed_from_terminal(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="終了後テスト",
        submission_close_at=_utc(7),
        voting_close_at=_utc(14),
    )
    await kukai_service.cancel(db_session, kukai)
    await db_session.commit()

    with pytest.raises(InvalidStateError):
        await kukai_service.proceed(db_session, kukai)
