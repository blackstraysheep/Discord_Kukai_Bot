"""Unit tests for kukai_service and state_machine."""

from datetime import datetime, timedelta, timezone

import pytest

from bot.models.kukai import Kukai
from bot.models.select_rule import SelectLabel
from bot.services import kukai_service
from bot.services.errors import DeadlineConflictError, InvalidStateError, NotFoundError, ValidationError
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
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    await db_session.commit()

    from sqlalchemy import select
    labels = (
        await db_session.execute(
            select(SelectLabel).where(SelectLabel.kukai_id == kukai.id)
        )
    ).scalars().all()

    assert len(labels) == 4
    assert {l.label for l in labels} == {"特選", "並選", "予選", "作者コメント"}
    points = {l.label: l.point for l in labels}
    assert points["特選"] == 2
    assert points["並選"] == 1
    assert points["予選"] == 0
    assert points["作者コメント"] == 0


@pytest.mark.asyncio
async def test_create_kukai_can_set_submission_max_unlimited(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="無制限句会",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        submission_max=None,
    )
    await db_session.commit()
    await db_session.refresh(kukai)

    assert kukai.submission_max is None


@pytest.mark.asyncio
async def test_create_kukai_deadline_conflict(db_session):
    with pytest.raises(DeadlineConflictError):
        await kukai_service.create_kukai(
            db_session,
            guild_id=1,
            created_by=100,
            channel_id=200,
            title="締切逆転",
            entry_close_at=_utc(3),
            submission_close_at=_utc(14),
            selecting_close_at=_utc(7),  # before submission
        )


@pytest.mark.asyncio
async def test_create_kukai_requires_entry_close_at_when_entry_enabled(db_session):
    with pytest.raises(ValidationError):
        await kukai_service.create_kukai(
            db_session,
            guild_id=1,
            created_by=100,
            channel_id=200,
            title="エントリー締切なし",
            submission_close_at=_utc(7),
            selecting_close_at=_utc(14),
            entry_enabled=True,
        )


@pytest.mark.asyncio
async def test_get_kukai_wrong_guild(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="別ギルド",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
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
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    await db_session.commit()
    assert kukai.state == KukaiState.ENTRY_OPEN

    new_state = await kukai_service.proceed(db_session, kukai)
    assert new_state == KukaiState.ENTRY_CLOSED
    assert kukai.state == KukaiState.ENTRY_CLOSED


@pytest.mark.asyncio
async def test_create_kukai_skip_entry_starts_submission_open(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="エントリースキップ",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=False,
    )
    await db_session.commit()

    assert kukai.state == KukaiState.SUBMISSION_OPEN
    new_state = await kukai_service.proceed(db_session, kukai)
    assert new_state == KukaiState.SUBMISSION_CLOSED


@pytest.mark.asyncio
async def test_pause_and_resume(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="ポーズテスト",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
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
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
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
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    await kukai_service.cancel(db_session, kukai)
    await db_session.commit()

    with pytest.raises(InvalidStateError):
        await kukai_service.proceed(db_session, kukai)


@pytest.mark.asyncio
async def test_list_kukais_excludes_results_state(db_session):
    active = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="開催中",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    active.state = KukaiState.SELECTING_OPEN

    results = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="結果公開中",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    results.state = KukaiState.RESULTS

    ended = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="終了済み",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    ended.state = KukaiState.ENDED

    await db_session.commit()

    listed = await kukai_service.list_kukais(db_session, guild_id=1)
    listed_ids = {k.id for k in listed}

    assert active.id in listed_ids
    assert results.id not in listed_ids
    assert ended.id not in listed_ids
