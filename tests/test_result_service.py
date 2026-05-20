"""Unit tests for result_service."""

from datetime import timedelta, timezone, datetime

import pytest

from bot.services import kukai_service, result_service, submission_service, select_service
from bot.services.errors import InvalidStateError
from bot.state_machine.states import KukaiState


def _utc(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


async def _setup_results(session, selects_spec):
    """
    Build a kukai at RESULTS state.

    selects_spec: list of (submitter_user_id, selector_user_id, label_index)
      label_index: 0=特選(2pt), 1=並選(1pt), 2=予選(0pt)
    """
    kukai = await kukai_service.create_kukai(
        session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="テスト句会",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=False,
    )

    labels = kukai.select_labels  # loaded by create_kukai

    # Advance to submission_open
    while KukaiState(kukai.state) != KukaiState.SUBMISSION_OPEN:
        await kukai_service.proceed(session, kukai)

    # Collect unique submitters
    submitters = list(dict.fromkeys(uid for uid, _, _ in selects_spec))
    texts = {uid: f"俳句_{uid}" for uid in submitters}
    for uid in submitters:
        await submission_service.submit(session, kukai, user_id=uid, text=texts[uid])

    # Advance to waiting_publish
    await kukai_service.proceed(session, kukai)  # → submission_closed
    await kukai_service.proceed(session, kukai)  # → waiting_publish
    await submission_service.publish(session, kukai)
    await kukai_service.proceed(session, kukai)  # → selecting_open
    await session.commit()

    # Cast selects
    for submitter_uid, selector_uid, label_idx in selects_spec:
        from bot.repositories import submission_repo
        pub = await submission_repo.list_published(session, kukai.id)
        target_sub = next(
            ps.submission_id for ps in pub if ps.submission.user_id == submitter_uid
        )
        label_id = labels[label_idx].id
        await select_service.cast_select(session, kukai, selector_uid, target_sub, label_id)

    # Advance to results
    await kukai_service.proceed(session, kukai)  # → selecting_closed
    await kukai_service.proceed(session, kukai)  # → waiting_results
    await kukai_service.proceed(session, kukai)  # → results
    await session.commit()

    return kukai


@pytest.mark.asyncio
async def test_compute_results_basic(db_session):
    # user 2 gets 特選(2pt) + 並選(1pt) = 3pt
    # user 3 gets 並選(1pt) = 1pt
    kukai = await _setup_results(db_session, [
        (2, 10, 0),  # selector 10 gives 特選 to user 2's haiku
        (2, 11, 1),  # selector 11 gives 並選 to user 2's haiku
        (3, 10, 1),  # selector 10 gives 並選 to user 3's haiku
    ])

    results = await result_service.compute_results(db_session, kukai)
    assert len(results) == 2
    assert results[0].author_user_id == 2
    assert results[0].total_score == 3
    assert results[0].rank == 1
    assert results[1].author_user_id == 3
    assert results[1].total_score == 1
    assert results[1].rank == 2


@pytest.mark.asyncio
async def test_compute_results_tie_breaking(db_session):
    """Tied by total score; more 特選 (rank_priority=1) wins."""
    # Both user 2 and user 3 get 2pt
    # user 2 gets 特選(2pt), user 3 gets 並選(1pt)+並選(1pt) = 2pt
    kukai = await _setup_results(db_session, [
        (2, 10, 0),  # selector 10: 特選 → user 2 (+2pt)
        (3, 11, 1),  # selector 11: 並選 → user 3 (+1pt)
        (3, 12, 1),  # selector 12: 並選 → user 3 (+1pt)
    ])

    results = await result_service.compute_results(db_session, kukai)
    assert results[0].total_score == 2
    assert results[1].total_score == 2
    # user 2 has 特選 → should win tiebreak
    assert results[0].author_user_id == 2
    assert results[0].rank == 1
    assert results[1].rank == 2


@pytest.mark.asyncio
async def test_compute_results_uses_custom_rank_priority_for_ties(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="rankテスト",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=False,
        select_label_specs=[
            {"label": "低優先", "point": 1, "rank_priority": 2},
            {"label": "高優先", "point": 1, "rank_priority": 1},
        ],
    )
    labels = {label.label: label for label in kukai.select_labels}

    while KukaiState(kukai.state) != KukaiState.SUBMISSION_OPEN:
        await kukai_service.proceed(db_session, kukai)

    await submission_service.submit(db_session, kukai, user_id=2, text="低優先の句")
    await submission_service.submit(db_session, kukai, user_id=3, text="高優先の句")
    await kukai_service.proceed(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)
    await submission_service.publish(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)

    from bot.repositories import submission_repo

    pub = await submission_repo.list_published(db_session, kukai.id)
    by_author = {ps.submission.user_id: ps.submission_id for ps in pub}
    await select_service.cast_select(
        db_session,
        kukai,
        selector_user_id=10,
        submission_id=by_author[2],
        select_label_id=labels["低優先"].id,
    )
    await select_service.cast_select(
        db_session,
        kukai,
        selector_user_id=11,
        submission_id=by_author[3],
        select_label_id=labels["高優先"].id,
    )
    await kukai_service.proceed(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)

    results = await result_service.compute_results(db_session, kukai)
    assert [result.author_user_id for result in results] == [3, 2]
    assert [result.rank for result in results] == [1, 2]


@pytest.mark.asyncio
async def test_compute_results_exact_tie_same_rank(db_session):
    """Exactly tied submissions get the same rank."""
    # user 2 and user 3 both get 特選(2pt) from different selectors
    kukai = await _setup_results(db_session, [
        (2, 10, 0),  # selector 10: 特選 → user 2 (+2pt)
        (3, 11, 0),  # selector 11: 特選 → user 3 (+2pt)
    ])

    results = await result_service.compute_results(db_session, kukai)
    assert results[0].total_score == 2
    assert results[1].total_score == 2
    assert results[0].rank == results[1].rank == 1


@pytest.mark.asyncio
async def test_compute_results_wrong_state_raises(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="テスト",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    # Not in results yet
    with pytest.raises(InvalidStateError):
        await result_service.compute_results(db_session, kukai)


@pytest.mark.asyncio
async def test_compute_results_no_selects(db_session):
    """Submission with no selects gets score=0."""
    kukai2 = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="テスト2",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=False,
    )

    while KukaiState(kukai2.state) != KukaiState.SUBMISSION_OPEN:
        await kukai_service.proceed(db_session, kukai2)

    await submission_service.submit(db_session, kukai2, user_id=1, text="春の海")
    await kukai_service.proceed(db_session, kukai2)  # → submission_closed
    await kukai_service.proceed(db_session, kukai2)  # → waiting_publish
    await submission_service.publish(db_session, kukai2)
    await kukai_service.proceed(db_session, kukai2)  # → selecting_open
    await kukai_service.proceed(db_session, kukai2)  # → selecting_closed
    await kukai_service.proceed(db_session, kukai2)  # → waiting_results
    await kukai_service.proceed(db_session, kukai2)  # → results
    await db_session.commit()

    results = await result_service.compute_results(db_session, kukai2)  # type: ignore[arg-type]
    assert len(results) == 1
    assert results[0].total_score == 0
    assert results[0].rank == 1


@pytest.mark.asyncio
async def test_compute_results_label_selects_populated(db_session):
    kukai = await _setup_results(db_session, [
        (2, 10, 0),  # 特選
        (2, 11, 0),  # 特選
        (2, 12, 1),  # 並選
    ])

    results = await result_service.compute_results(db_session, kukai)
    assert len(results) == 1
    r = results[0]
    assert r.total_score == 5  # 2+2+1
    label_map = {lv.label: lv for lv in r.label_selects}
    assert label_map["特選"].count == 2
    assert label_map["並選"].count == 1
