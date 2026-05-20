"""Phase 10 tests for notification scheduling and deadline jobs."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import bot.database as database_mod
from bot.models.notification import NotificationSchedule
from bot.scheduler import jobs
from bot.services import entry_service, kukai_service, notification_service, voice_service
from bot.state_machine.states import KukaiState


def _utc(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


async def _make_kukai(session, *, entry_enabled: bool = False):
    kukai = await kukai_service.create_kukai(
        session,
        guild_id=1,
        created_by=100,
        channel_id=200,
        title="通知テスト句会",
        submission_close_at=_utc(7),
        selecting_close_at=_utc(14),
        entry_enabled=entry_enabled,
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

    called = {"channel": [], "admins": []}

    async def _fake_notify_channel(bot, kukai, message: str):
        called["channel"].append((kukai.id, message))

    async def _fake_notify_admins(bot, kukai, message: str):
        called["admins"].append((kukai.id, message))

    monkeypatch.setattr(jobs, "_notify_channel", _fake_notify_channel)
    monkeypatch.setattr(jobs, "_notify_admins", _fake_notify_admins)
    return called


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
    await kukai_service.proceed(db_session, kukai)  # entry_closed
    await kukai_service.proceed(db_session, kukai)  # submission_open

    kukai.submission_mode = "semi_auto"
    await db_session.flush()

    called = _patch_deadline_env(monkeypatch, db_session)
    await jobs.deadline_job(kukai.id, "submission_close")

    assert KukaiState(kukai.state) == KukaiState.SUBMISSION_OPEN
    assert called["channel"] == []
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
