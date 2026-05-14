from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(database_url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_engine():
    return _engine
