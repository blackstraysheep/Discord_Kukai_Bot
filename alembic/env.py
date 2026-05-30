import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make sure the project root is on sys.path so bot.* imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bot.models.base import Base
import bot.models  # noqa: F401 — registers all ORM models with Base.metadata
from bot.settings import _to_sync_db_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    """Read the DB URL from Settings (honours .env) and return async variant."""
    try:
        from bot.settings import get_settings
        return get_settings().database_url
    except Exception:
        # Fall back to alembic.ini value; ensure it uses the async driver
        url = config.get_main_option("sqlalchemy.url", "sqlite:///./data/kukai.db")
        if url.startswith("sqlite:///") and "aiosqlite" not in url:
            url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return url


def run_migrations_offline() -> None:
    url = _to_sync_db_url(_get_url())
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()

    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
