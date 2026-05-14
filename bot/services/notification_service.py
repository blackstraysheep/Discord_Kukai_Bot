"""Notification scheduling service.

Manages APScheduler jobs for kukai deadline notifications and auto-advance.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.notification import NotificationSchedule
from bot.scheduler.setup import get_scheduler, has_scheduler

logger = logging.getLogger(__name__)

# Default: send reminder 24 h before each deadline
_DEFAULT_OFFSETS: list[tuple[str, int]] = [
    ("submission_close", 86400),
    ("selecting_close", 86400),
]


def _get_deadline_dt(kukai, event_type: str) -> datetime | None:
    if event_type == "submission_close":
        return kukai.submission_close_at
    if event_type == "selecting_close":
        return kukai.selecting_close_at
    if event_type == "entry_close":
        return getattr(kukai, "entry_close_at", None)
    return None


async def _ensure_default_schedules(session: AsyncSession, kukai) -> None:
    existing = await session.execute(
        select(NotificationSchedule).where(NotificationSchedule.kukai_id == kukai.id)
    )
    if list(existing.scalars().all()):
        return  # already created

    for event_type, offset_secs in _DEFAULT_OFFSETS:
        if _get_deadline_dt(kukai, event_type) is not None:
            session.add(
                NotificationSchedule(
                    kukai_id=kukai.id,
                    event_type=event_type,
                    offset_secs=offset_secs,
                )
            )


async def schedule_kukai_jobs(session: AsyncSession, kukai) -> None:
    """Create default notification schedules and register APScheduler jobs."""
    if not has_scheduler():
        return

    # lazy import avoids circular import at module level
    from bot.scheduler.jobs import deadline_job, notification_job

    scheduler = get_scheduler()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    await _ensure_default_schedules(session, kukai)
    await session.flush()

    # Schedule notification jobs for all unfired schedules
    ns_result = await session.execute(
        select(NotificationSchedule).where(
            NotificationSchedule.kukai_id == kukai.id,
            NotificationSchedule.fired == False,
        )
    )
    for ns in ns_result.scalars().all():
        deadline_dt = _get_deadline_dt(kukai, ns.event_type)
        if deadline_dt is None:
            continue
        fire_at = deadline_dt - timedelta(seconds=ns.offset_secs)
        if fire_at <= now:
            continue

        job_id = f"notify_{ns.id}"
        scheduler.add_job(
            notification_job,
            trigger="date",
            run_date=fire_at,
            args=[ns.id],
            id=job_id,
            replace_existing=True,
        )
        ns.job_id = job_id
        logger.info("Scheduled notification job %s at %s", job_id, fire_at)

    # Schedule deadline jobs
    for event_type, deadline_dt in [
        ("submission_close", kukai.submission_close_at),
        ("selecting_close", kukai.selecting_close_at),
    ]:
        if deadline_dt is None or deadline_dt <= now:
            continue
        job_id = f"deadline_{kukai.id}_{event_type}"
        scheduler.add_job(
            deadline_job,
            trigger="date",
            run_date=deadline_dt,
            args=[kukai.id, event_type],
            id=job_id,
            replace_existing=True,
        )
        logger.info("Scheduled deadline job %s at %s", job_id, deadline_dt)


async def cancel_kukai_jobs(session: AsyncSession, kukai_id: int) -> None:
    """Remove all APScheduler jobs for a kukai (pause or cancel)."""
    if not has_scheduler():
        return

    from apscheduler.jobstores.base import JobLookupError

    scheduler = get_scheduler()

    ns_result = await session.execute(
        select(NotificationSchedule).where(NotificationSchedule.kukai_id == kukai_id)
    )
    for ns in ns_result.scalars().all():
        if ns.job_id:
            try:
                scheduler.remove_job(ns.job_id)
                logger.info("Removed notification job %s", ns.job_id)
            except JobLookupError:
                pass

    for event_type in ("submission_close", "selecting_close"):
        job_id = f"deadline_{kukai_id}_{event_type}"
        try:
            scheduler.remove_job(job_id)
            logger.info("Removed deadline job %s", job_id)
        except JobLookupError:
            pass
