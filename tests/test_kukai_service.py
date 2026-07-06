"""Unit tests for kukai_service and state_machine."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.models.kukai import Kukai
from bot.models.select import OverallSelectComment, Select
from bot.models.select_rule import SelectLabel
from bot.models.submission import Submission
from bot.services import kukai_service, proceed_service
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
async def test_create_kukai_can_set_channel_visibility_policy(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="クローズド句会",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        channel_visibility_policy="public_until_participation_close",
    )

    assert kukai.channel_visibility_policy == "public_until_participation_close"


@pytest.mark.asyncio
async def test_create_kukai_rejects_invalid_channel_visibility_policy(db_session):
    with pytest.raises(ValidationError):
        await kukai_service.create_kukai(
            db_session,
            guild_id=1,
            created_by=100,
            channel_id=200,
            title="不正な閲覧モード",
            entry_close_at=_utc(3),
            submission_close_at=_utc(7),
            selecting_close_at=_utc(14),
            channel_visibility_policy="private",
        )


@pytest.mark.asyncio
async def test_create_kukai_manual_author_publication_keeps_zero_policy(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="作者後公開句会",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        author_publication_mode="manual",
        author_reveal_zero=False,
    )

    assert kukai.author_publication_mode == "manual"
    assert kukai.author_reveal is False
    assert kukai.author_reveal_zero is False


@pytest.mark.asyncio
async def test_create_kukai_never_author_publication_disables_zero_policy(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="作者非公開句会",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        author_publication_mode="never",
        author_reveal_zero=False,
    )

    assert kukai.author_publication_mode == "never"
    assert kukai.author_reveal is False
    assert kukai.author_reveal_zero is True


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
async def test_create_kukai_allows_entry_enabled_without_explicit_entry_close(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="エントリー締切なし",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=True,
    )

    assert kukai.entry_enabled is True
    assert kukai.entry_close_at is None


@pytest.mark.asyncio
async def test_create_kukai_allows_entry_close_equal_submission_close(db_session):
    close_at = _utc(7)
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="同時締切",
        entry_close_at=close_at,
        submission_close_at=close_at,
        selecting_close_at=_utc(14),
        entry_enabled=True,
    )

    assert kukai.entry_close_at == kukai.submission_close_at


@pytest.mark.asyncio
async def test_create_kukai_normalizes_legacy_entry_full_auto(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="旧エントリーモード",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=True,
        entry_mode="full_auto",
    )

    assert kukai.entry_mode == "auto"


@pytest.mark.asyncio
async def test_create_kukai_validates_submission_open_at(db_session):
    close_at = _utc(7)
    with pytest.raises(DeadlineConflictError):
        await kukai_service.create_kukai(
            db_session,
            guild_id=1,
            created_by=100,
            channel_id=200,
            title="投句開始逆転",
            submission_open_at=close_at,
            submission_close_at=close_at,
            selecting_close_at=_utc(14),
            entry_enabled=False,
        )


@pytest.mark.asyncio
async def test_edit_kukai_updates_newly_exposed_settings(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="編集対象追加",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    new_entry_close = _utc(4)

    deadlines_changed = await kukai_service.edit_kukai(
        db_session,
        kukai,
        entry_close_at=new_entry_close,
        entry_approval=True,
        min_participants=3,
        submission_overflow=True,
        result_mode="auto",
    )

    assert deadlines_changed is True
    assert kukai.entry_close_at == new_entry_close
    assert kukai.entry_approval is True
    assert kukai.min_participants == 3
    assert kukai.submission_overflow is True
    assert kukai.result_mode == "auto"


@pytest.mark.asyncio
async def test_edit_kukai_validates_entry_close_at(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="エントリー締切逆転",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )

    with pytest.raises(DeadlineConflictError):
        await kukai_service.edit_kukai(db_session, kukai, entry_close_at=_utc(8))


@pytest.mark.asyncio
async def test_edit_kukai_manual_author_publication_resets_revealed_authors(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="作者公開戻し",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        author_publication_mode="with_result",
    )
    kukai.state = KukaiState.RESULTS
    kukai.author_reveal = True

    await kukai_service.edit_kukai(
        db_session,
        kukai,
        author_publication_mode="manual",
    )

    assert kukai.author_publication_mode == "manual"
    assert kukai.author_reveal is False


@pytest.mark.asyncio
async def test_edit_kukai_never_author_publication_resets_zero_policy(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="作者非公開戻し",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        author_publication_mode="with_result",
        author_reveal_zero=False,
    )
    kukai.state = KukaiState.RESULTS
    kukai.author_reveal = True

    await kukai_service.edit_kukai(
        db_session,
        kukai,
        author_publication_mode="never",
    )

    assert kukai.author_publication_mode == "never"
    assert kukai.author_reveal is False
    assert kukai.author_reveal_zero is True


@pytest.mark.asyncio
async def test_edit_kukai_author_reveal_true_switches_never_to_manual(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="作者手動公開",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        author_publication_mode="never",
    )

    await kukai_service.edit_kukai(db_session, kukai, author_reveal=True)

    assert kukai.author_publication_mode == "manual"
    assert kukai.author_reveal is True


@pytest.mark.asyncio
async def test_replace_select_rules_replaces_labels_before_selecting(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="選句ルール差し替え",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )

    labels = await kukai_service.replace_select_rules(
        db_session,
        kukai,
        select_label_specs=[
            {
                "label": "二重丸",
                "point": 3,
                "min_count": 1,
                "max_count": 2,
                "comment_mode": "required",
            }
        ],
        points_enabled=True,
    )

    assert kukai.points_enabled is True
    assert {label.label for label in labels} == {"二重丸", "作者コメント"}
    stored = (
        await db_session.execute(
            select(SelectLabel).where(SelectLabel.kukai_id == kukai.id)
        )
    ).scalars().all()
    assert {label.label for label in stored} == {"二重丸", "作者コメント"}


@pytest.mark.asyncio
async def test_replace_select_rules_clears_existing_select_data_when_confirmed(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="残存選句あり",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    label = (
        await db_session.execute(
            select(SelectLabel)
            .where(SelectLabel.kukai_id == kukai.id, SelectLabel.label == "特選")
        )
    ).scalar_one()
    submission = Submission(kukai_id=kukai.id, user_id=1001, text="古池や")
    db_session.add(submission)
    await db_session.flush()
    db_session.add(
        Select(
            kukai_id=kukai.id,
            selector_user_id=1002,
            submission_id=submission.id,
            select_label_id=label.id,
        )
    )
    db_session.add(OverallSelectComment(kukai_id=kukai.id, user_id=1002, comment="総評"))
    await db_session.flush()

    assert await kukai_service.count_select_rule_data(db_session, kukai.id) == (1, 1)
    with pytest.raises(ValidationError):
        await kukai_service.replace_select_rules(
            db_session,
            kukai,
            select_label_specs=[{"label": "並", "point": 1, "min_count": 0, "max_count": 3}],
            points_enabled=False,
        )

    labels = await kukai_service.replace_select_rules(
        db_session,
        kukai,
        select_label_specs=[{"label": "並", "point": 1, "min_count": 0, "max_count": 3}],
        points_enabled=False,
        clear_existing_select_data=True,
    )

    assert await kukai_service.count_select_rule_data(db_session, kukai.id) == (0, 0)
    assert kukai.points_enabled is False
    assert {label.label: label.point for label in labels} == {"並": 0, "作者コメント": 0}


@pytest.mark.asyncio
async def test_replace_select_rules_rejects_after_selecting_started(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="選句開始後",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    kukai.state = KukaiState.SELECTING_OPEN

    with pytest.raises(InvalidStateError):
        await kukai_service.replace_select_rules(
            db_session,
            kukai,
            select_label_specs=[{"label": "並", "point": 1, "min_count": 0, "max_count": 3}],
            points_enabled=True,
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
    assert new_state == KukaiState.SUBMISSION_OPEN
    assert kukai.state == KukaiState.SUBMISSION_OPEN


@pytest.mark.asyncio
async def test_proceed_preview_does_not_mutate_state(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="進行プレビュー",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )
    kukai.state = KukaiState.SUBMISSION_CLOSED

    preview = await proceed_service.preview_proceed(db_session, kukai)

    assert preview.current_state == KukaiState.SUBMISSION_CLOSED
    assert preview.next_state == KukaiState.SELECTING_OPEN
    assert kukai.state == KukaiState.SUBMISSION_CLOSED
    assert "投句一覧を番号付きで投稿します。" in preview.effects


@pytest.mark.asyncio
async def test_proceed_preview_reports_current_and_next_labels(db_session):
    kukai = await kukai_service.create_kukai(
        db_session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="状態表示",
        entry_close_at=_utc(3),
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
    )

    preview = await proceed_service.preview_proceed(db_session, kukai)

    assert proceed_service.state_label(preview.current_state) == "エントリー受付中"
    assert proceed_service.state_label(preview.next_state) == "投句受付中"


@pytest.mark.asyncio
async def test_create_kukai_skip_entry_starts_draft_before_submission(db_session):
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

    assert kukai.state == KukaiState.DRAFT
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
