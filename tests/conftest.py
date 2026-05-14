"""Shared pytest fixtures."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.models.base import Base
import bot.models  # noqa: F401


@pytest_asyncio.fixture
async def db_session():
    """In-memory async SQLite session for unit tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
