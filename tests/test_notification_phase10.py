"""Phase 10 tests for notification scheduling and deadline jobs."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import bot.database as database_mod
import bot.utils.stage_announcement as stage_announcement_mod
from bot.models.notification import NotificationSchedule
from bot.scheduler import jobs
from bot.services import admin_notice_service, entry_service, kukai_service, notification_service, voice_service
from bot.state_machine.states import KukaiState


def _utc(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


async def _make_kukai(session, *, entry_enabled: bool = False, **kwargs):
    entry_close_at = kwargs.pop("entry_close_at", _utc(3) if entry_enabled else None)
    submission_close_at = kwargs.pop("submission_close_at", _utc(7))
    selecting_close_at = kwargs.pop("selecting_close_at", _utc(14))
    kukai = await kukai_service.create_kukai(
        session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="通知テスト句会",
        entry_close_at=entry_close_at,
        submission_close_at=submission_close_at,
        selecting_close_at=selecting_close_at,
        entry_enabled=entry_enabled,
        **kwargs,
    )
    await session.flush()
    return kukai


class _FakeScheduler:
    def __init__(self) -> None:
        self.added: list[dict] = []
        self.removed: list[str] = []

    def add_job(self, func, trigger, run_date, args, id, replace_existing):  # noqa: A002
        self.added.append(
            {
                "func": func,
                "trigger": trigger,
                "run_date": run_date,
                "args": args,
                "id": id,
                "replace_existing": replace_existing,
            }
        )

    def remove_job(self, job_id: str) -> None:
        self.removed.append(job_id)


class _FakeBot:
    async def wait_until_ready(self) -> None:
        return None

    def get_guild(self, guild_id: int):
        return None


class _GuildForAdminNotify:
    def __init__(self, member, channel) -> None:
        self._member = member
        self._channel = channel

    def get_member(self, user_id: int):
        return self._member if self._member.id == user_id else None

    def get_channel(self, channel_id: int):
        return self._channel if self._channel.id == channel_id else None


class _MemberFailsDM:
    def __init__(self, user_id: int) -> None:
        self.id = user_id

    async def send(self, message: str):
        raise RuntimeError("dm blocked")


class _ChannelCapture:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: list[str] = []

    async def send(self, message: str):
        self.messages.append(message)


class _EmbedChannelCapture:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent: list[dict] = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=len(self.sent))


class _MemberWithDisplayName:
    def __init__(self, user_id: int, display_name: str) -> None:
        self.id = user_id
        self.display_name = display_name


class _GuildForEntryClose:
    def __init__(self, channel, members=None) -> None:
        self._channel = channel
        self._members = {member.id: member for member in members or []}

    def get_member(self, user_id: int):
        return self._members.get(user_id)

    def get_channel(self, channel_id: int):
        return self._channel if self._channel.id == channel_id else None


class _BotWithGuild:
    def __init__(self, guild) -> None:
        self._guild = guild

    def get_guild(self, guild_id: int):
        return self._guild


@pytest.mark.asyncio
async def test_schedule_kukai_jobs_registers_notification_and_deadline_jobs(db_session, monkeypatch):
    kukai = await _make_kukai(db_session)
    scheduler = _FakeScheduler()
    monkeypatch.setattr(notification_service, "has_scheduler", lambda: True)
    monkeypatch.setattr(notification_service, "get_scheduler", lambda: scheduler)

    await notification_service.schedule_kukai_jobs(db_session, kukai)

    schedules = (
        await db_session.execute(
            select(NotificationSchedule).where(NotificationSchedule.kukai_id == kukai.id)
        )
    ).scalars().all()
    assert len(schedules) == 2
    assert all(s.job_id is not None for s in schedules)

    added_ids = {item["id"] for item in scheduler.added}
    assert any(job_id.startswith("notify_") for job_id in added_ids)
    assert f"deadline_{kukai.id}_submission_close" in added_ids
    assert f"deadline_{kukai.id}_selecting_close" in added_ids


@pytest.mark.asyncio
async def test_schedule_kukai_jobs_runs_entry_close_before_equal_submission_close(
    db_session, monkeypatch
):
    close_at = _utc(7)
    kukai = await _make_kukai(
        db_session,
        entry_enabled=True,
        entry_mode="auto",
        entry_close_at=close_at,
        submission_open_at=_utc(3),
        submission_close_at=close_at,
    )
    scheduler = _FakeScheduler()
    monkeypatch.setattr(notification_service, "has_scheduler", lambda: True)
    monkeypatch.setattr(notification_service, "get_scheduler", lambda: scheduler)

    await notification_service.schedule_kukai_jobs(db_session, kukai)

    deadline_jobs = {
        item["args"][1]: item
        for item in scheduler.added
        if item["id"].startswith(f"deadline_{kukai.id}_")
    }
    assert deadline_jobs["entry_close"]["run_date"] < deadline_jobs["submission_close"]["run_date"]
    assert deadline_jobs["submission_close"]["run_date"] == close_at


@pytest.mark.asyncio
async def test_custom_voice_notification_schedule_registers_job(db_session, monkeypatch):
    kukai = await _make_kukai(db_session)
    await voice_service.upsert_voice_session(
        db_session,
        kukai,
        vc_channel_id=300,
        start_at=_utc(21),
        end_at=_utc(22),
    )
    await notification_service.replace_notification_schedules(
        db_session,
        kukai,
        [
            {
                "event_type": "voice_start",
                "offset_secs": 1800,
                "channel_id": -1,
                "target": "all",
                "mention": False,
            },
            {
                "event_type": "selecting_close",
                "offset_secs": 3600,
                "channel_id": 250,
                "target": "incomplete",
                "mention": True,
            },
        ],
    )
    scheduler = _FakeScheduler()
    monkeypatch.setattr(notification_service, "has_scheduler", lambda: True)
    monkeypatch.setattr(notification_service, "get_scheduler", lambda: scheduler)

    await notification_service.schedule_kukai_jobs(db_session, kukai)

    schedules = (
        await db_session.execute(
            select(NotificationSchedule).where(NotificationSchedule.kukai_id == kukai.id)
        )
    ).scalars().all()
    assert len(schedules) == 2
    assert {s.event_type for s in schedules} == {"voice_start", "selecting_close"}
    assert any(s.channel_id == -1 for s in schedules)
    assert any(s.mention for s in schedules)
    assert sum(1 for item in scheduler.added if item["id"].startswith("notify_")) == 2


@pytest.mark.asyncio
async def test_cancel_kukai_jobs_removes_registered_jobs(db_session, monkeypatch):
    kukai = await _make_kukai(db_session)
    scheduler = _FakeScheduler()
    monkeypatch.setattr(notification_service, "has_scheduler", lambda: True)
    monkeypatch.setattr(notification_service, "get_scheduler", lambda: scheduler)

    await notification_service.schedule_kukai_jobs(db_session, kukai)
    await notification_service.cancel_kukai_jobs(db_session, kukai.id)

    removed = set(scheduler.removed)
    assert f"deadline_{kukai.id}_submission_close" in removed
    assert f"deadline_{kukai.id}_selecting_close" in removed
    assert any(job_id.startswith("notify_") for job_id in removed)


def _patch_deadline_env(monkeypatch, db_session):
    @asynccontextmanager
    async def _fake_get_session():
        yield db_session

    monkeypatch.setattr(database_mod, "get_session", _fake_get_session)
    jobs.set_bot(_FakeBot())

    called = {"channel": [], "admins": [], "submission_open": []}

    async def _fake_notify_channel(bot, kukai, message: str):
        called["channel"].append((kukai.id, message))

    async def _fake_notify_admins(bot, kukai, message: str):
        called["admins"].append((kukai.id, message))

    monkeypatch.setattr(jobs, "_notify_channel", _fake_notify_channel)
    monkeypatch.setattr(jobs, "_notify_admins", _fake_notify_admins)

    async def _fake_notify_submission_open(*, bot, kukai):
        called["submission_open"].append(kukai.id)

    monkeypatch.setattr(jobs, "_notify_submission_open", _fake_notify_submission_open)

    async def _fake_send_admin_notice(bot, session, kukai, **kwargs):
        called["admins"].append((kukai.id, kwargs))
        return True

    monkeypatch.setattr(admin_notice_service, "send_admin_notice", _fake_send_admin_notice)
    return called


@pytest.mark.asyncio
async def test_deadline_job_entry_close_auto_opens_submission_with_approved_entry(
    db_session, monkeypatch
):
    kukai = await _make_kukai(db_session, entry_enabled=True, entry_mode="auto")
    await entry_service.enter(db_session, kukai, user_id=101, haigo="山田太郎")
    await db_session.flush()

    called = _patch_deadline_env(monkeypatch, db_session)
    await jobs.deadline_job(kukai.id, "entry_close")

    assert KukaiState(kukai.state) == KukaiState.SUBMISSION_OPEN
    assert called["submission_open"] == [kukai.id]


@pytest.mark.asyncio
async def test_notify_entry_closed_includes_approved_count_and_haigo(db_session):
    kukai = await _make_kukai(db_session, entry_enabled=True, entry_mode="auto")
    await entry_service.enter(db_session, kukai, user_id=101, haigo="山田太郎")
    await entry_service.enter(db_session, kukai, user_id=102, haigo="testaro")
    await entry_service.enter(db_session, kukai, user_id=103, haigo="☆之助")
    pending_entry = await entry_service.enter(db_session, kukai, user_id=104, haigo=None)
    pending_entry.status = "pending"
    await db_session.flush()

    channel = _EmbedChannelCapture(channel_id=200)
    guild = _GuildForEntryClose(
        channel,
        members=[_MemberWithDisplayName(104, "表示名")],
    )
    bot = _BotWithGuild(guild)

    await jobs._notify_entry_closed(bot=bot, session=db_session, kukai=kukai)

    assert len(channel.sent) == 1
    embed = channel.sent[0]["embed"]
    assert "エントリーが締め切られました" in embed.description
    assert embed.fields[0].name == "エントリー人数"
    assert embed.fields[0].value == "3名（山田太郎、testaro、☆之助）"
    assert "allowed_mentions" in channel.sent[0]


@pytest.mark.asyncio
async def test_notify_selecting_open_includes_action_button(db_session, monkeypatch):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    while KukaiState.from_value(kukai.state) != KukaiState.SELECTING_OPEN:
        await kukai_service.proceed(db_session, kukai)
    await db_session.flush()

    @asynccontextmanager
    async def _fake_get_session():
        yield db_session

    monkeypatch.setattr(stage_announcement_mod, "get_session", _fake_get_session)
    channel = _EmbedChannelCapture(channel_id=200)
    guild = _GuildForEntryClose(channel)
    bot = _BotWithGuild(guild)

    await jobs._notify_selecting_open(bot=bot, kukai=kukai)

    assert len(channel.sent) == 1
    sent = channel.sent[0]
    assert "選句受付" in sent["embed"].description
    assert sent["view"].children[0].label == "選句する"
    assert sent["view"].children[0].custom_id == f"kukai:stage:{kukai.id}:selecting_open"


@pytest.mark.asyncio
async def test_deadline_job_entry_close_auto_cancels_without_approved_entries(
    db_session, monkeypatch
):
    kukai = await _make_kukai(db_session, entry_enabled=True, entry_mode="auto")
    called = _patch_deadline_env(monkeypatch, db_session)
    called["entry_closed"] = []

    async def _fake_notify_entry_closed(*, bot, session, kukai):
        called["entry_closed"].append(kukai.id)

    monkeypatch.setattr(jobs, "_notify_entry_closed", _fake_notify_entry_closed)

    await jobs.deadline_job(kukai.id, "entry_close")

    assert KukaiState(kukai.state) == KukaiState.CANCELLED
    assert called["entry_closed"] == [kukai.id]
    assert called["admins"]
    assert called["channel"]


@pytest.mark.asyncio
async def test_deadline_job_entry_close_auto_waits_for_submission_open_at(
    db_session, monkeypatch
):
    kukai = await _make_kukai(
        db_session,
        entry_enabled=True,
        entry_mode="auto",
        submission_open_at=_utc(5),
    )
    await entry_service.enter(db_session, kukai, user_id=101)
    await db_session.flush()

    called = _patch_deadline_env(monkeypatch, db_session)
    await jobs.deadline_job(kukai.id, "entry_close")

    assert KukaiState(kukai.state) == KukaiState.ENTRY_CLOSED
    assert called["submission_open"] == []

    await jobs.deadline_job(kukai.id, "submission_open")

    assert KukaiState(kukai.state) == KukaiState.SUBMISSION_OPEN
    assert called["submission_open"] == [kukai.id]


@pytest.mark.asyncio
async def test_manual_submission_close_sends_early_entry_close_notice_and_marks_fired(
    db_session, monkeypatch
):
    kukai = await _make_kukai(
        db_session,
        entry_enabled=True,
        entry_mode="auto",
        entry_close_at=_utc(7),
        submission_close_at=_utc(10),
    )
    await entry_service.enter(db_session, kukai, user_id=101, haigo="山田太郎")
    await kukai_service.proceed(db_session, kukai)
    previous_state = KukaiState.from_value(kukai.state)
    await kukai_service.proceed(db_session, kukai)
    schedule = NotificationSchedule(
        kukai_id=kukai.id,
        event_type="entry_close",
        offset_secs=86400,
        fired=False,
        job_id="notify-entry-close",
    )
    db_session.add(schedule)
    await db_session.flush()

    called = []

    async def _fake_notify_entry_closed(*, bot, session, kukai):
        called.append(kukai.id)

    monkeypatch.setattr(jobs, "_notify_entry_closed", _fake_notify_entry_closed)

    sent = await jobs.notify_entry_closed_for_manual_submission_close(
        bot=_FakeBot(),
        session=db_session,
        kukai=kukai,
        previous_state=previous_state,
    )

    assert sent is True
    assert called == [kukai.id]
    assert schedule.fired is True
    assert schedule.job_id is None


@pytest.mark.asyncio
async def test_schedule_kukai_jobs_does_not_register_entry_close_after_submission_closed(
    db_session, monkeypatch
):
    kukai = await _make_kukai(
        db_session,
        entry_enabled=True,
        entry_mode="auto",
        entry_close_at=_utc(7),
        submission_close_at=_utc(10),
    )
    await kukai_service.proceed(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)
    scheduler = _FakeScheduler()
    monkeypatch.setattr(notification_service, "has_scheduler", lambda: True)
    monkeypatch.setattr(notification_service, "get_scheduler", lambda: scheduler)

    await notification_service.schedule_kukai_jobs(db_session, kukai)

    deadline_events = {
        item["args"][1]
        for item in scheduler.added
        if item["id"].startswith(f"deadline_{kukai.id}_")
    }
    assert "entry_close" not in deadline_events
    assert "submission_close" not in deadline_events
    assert "selecting_close" in deadline_events


@pytest.mark.asyncio
async def test_schedule_kukai_jobs_marks_submission_close_reminder_past_after_manual_close(
    db_session, monkeypatch
):
    kukai = await _make_kukai(
        db_session,
        entry_enabled=True,
        entry_mode="auto",
        entry_close_at=_utc(7),
        submission_close_at=_utc(10),
    )
    await kukai_service.proceed(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)
    schedule = NotificationSchedule(
        kukai_id=kukai.id,
        event_type="submission_close",
        offset_secs=86400,
        fired=False,
        job_id="notify-submission-close",
    )
    db_session.add(schedule)
    await db_session.flush()
    scheduler = _FakeScheduler()
    monkeypatch.setattr(notification_service, "has_scheduler", lambda: True)
    monkeypatch.setattr(notification_service, "get_scheduler", lambda: scheduler)

    await notification_service.schedule_kukai_jobs(db_session, kukai)

    assert schedule.fired is True
    assert schedule.job_id is None
    assert "notify-submission-close" in scheduler.removed


@pytest.mark.asyncio
async def test_schedule_kukai_jobs_marks_selecting_close_past_after_manual_close(
    db_session, monkeypatch
):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    while KukaiState.from_value(kukai.state) != KukaiState.SELECTING_OPEN:
        await kukai_service.proceed(db_session, kukai)
    await kukai_service.proceed(db_session, kukai)
    schedule = NotificationSchedule(
        kukai_id=kukai.id,
        event_type="selecting_close",
        offset_secs=86400,
        fired=False,
        job_id="notify-selecting-close",
    )
    db_session.add(schedule)
    await db_session.flush()
    scheduler = _FakeScheduler()
    monkeypatch.setattr(notification_service, "has_scheduler", lambda: True)
    monkeypatch.setattr(notification_service, "get_scheduler", lambda: scheduler)

    await notification_service.schedule_kukai_jobs(db_session, kukai)

    deadline_events = {
        item["args"][1]
        for item in scheduler.added
        if item["id"].startswith(f"deadline_{kukai.id}_")
    }
    assert schedule.fired is True
    assert schedule.job_id is None
    assert "notify-selecting-close" in scheduler.removed
    assert "selecting_close" not in deadline_events


@pytest.mark.asyncio
async def test_deadline_job_submission_open_allows_entry_to_continue_until_entry_close(
    db_session, monkeypatch
):
    kukai = await _make_kukai(
        db_session,
        entry_enabled=True,
        entry_mode="auto",
        entry_close_at=_utc(7),
        submission_open_at=_utc(3),
        submission_close_at=_utc(10),
        selecting_close_at=_utc(14),
    )
    called = _patch_deadline_env(monkeypatch, db_session)

    await jobs.deadline_job(kukai.id, "submission_open")
    entry = await entry_service.enter(db_session, kukai, user_id=101, haigo="testaro")

    assert KukaiState(kukai.state) == KukaiState.SUBMISSION_OPEN
    assert entry.status == "approved"
    assert called["submission_open"] == [kukai.id]


@pytest.mark.asyncio
async def test_deadline_job_entry_close_without_explicit_deadline_does_not_auto_start(
    db_session, monkeypatch
):
    kukai = await _make_kukai(
        db_session,
        entry_enabled=True,
        entry_close_at=None,
        entry_mode="auto",
        submission_mode="full_auto",
    )
    await entry_service.enter(db_session, kukai, user_id=101)
    await db_session.flush()

    _patch_deadline_env(monkeypatch, db_session)
    await jobs.deadline_job(kukai.id, "submission_close")

    assert KukaiState(kukai.state) == KukaiState.ENTRY_OPEN


@pytest.mark.asyncio
async def test_deadline_job_submission_close_full_auto_advances(db_session, monkeypatch):
    kukai = await _make_kukai(db_session, entry_enabled=False)
    while KukaiState(kukai.state) != KukaiState.SUBMISSION_OPEN:
        await kukai_service.proceed(db_session, kukai)

    kukai.submission_mode = "full_auto"
    await db_session.flush()

    called = _patch_deadline_env(monkeypatch, db_session)
    await jobs.deadline_job(kukai.id, "submission_close")

    assert KukaiState(kukai.state) == KukaiState.SUBMISSION_CLOSED
    assert called["channel"]
    assert called["admins"] == []


@pytest.mark.asyncio
async def test_deadline_job_submission_close_semi_auto_incomplete_notifies_admins(
    db_session, monkeypatch
):
    kukai = await _make_kukai(db_session, entry_enabled=True)
    await entry_service.enter(db_session, kukai, user_id=101)
    await kukai_service.proceed(db_session, kukai)

    kukai.submission_mode = "semi_auto"
    await db_session.flush()

    called = _patch_deadline_env(monkeypatch, db_session)
    await jobs.deadline_job(kukai.id, "submission_close")

    assert KukaiState(kukai.state) == KukaiState.SUBMISSION_OPEN
    assert called["channel"]
    assert called["admins"]


@pytest.mark.asyncio
async def test_notify_admins_fallbacks_to_channel_when_dm_fails():
    member = _MemberFailsDM(user_id=100)
    channel = _ChannelCapture(channel_id=200)
    guild = _GuildForAdminNotify(member=member, channel=channel)
    bot = _BotWithGuild(guild)
    kukai = SimpleNamespace(guild_id=1, created_by=100, channel_id=200)

    await jobs._notify_admins(bot, kukai, "通知テスト")

    assert len(channel.messages) == 1
    assert "<@100>" in channel.messages[0]
