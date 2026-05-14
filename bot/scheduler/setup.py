"""APScheduler initialization."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

_scheduler: AsyncIOScheduler | None = None


def init_scheduler(sync_db_url: str) -> AsyncIOScheduler:
    global _scheduler
    jobstores = {"default": SQLAlchemyJobStore(url=sync_db_url)}
    _scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
    return _scheduler


def get_scheduler() -> AsyncIOScheduler:
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized. Call init_scheduler() first.")
    return _scheduler


def has_scheduler() -> bool:
    return _scheduler is not None
