from bot.settings import Settings


def test_sync_db_url_keeps_sqlite_default_sync_driver():
    settings = Settings(_env_file=None, bot_token="token")

    assert settings.sync_db_url == "sqlite:///./data/kukai.db"
    assert settings.scheduler_sync_db_url == "sqlite:///./data/kukai_scheduler.db"


def test_sync_db_url_converts_postgresql_asyncpg_to_psycopg():
    settings = Settings(
        bot_token="token",
        database_url="postgresql+asyncpg://user:pass@db:5432/kukai",
        scheduler_db_url="postgresql+asyncpg://user:pass@db:5432/kukai_scheduler",
    )

    assert settings.sync_db_url == "postgresql+psycopg://user:pass@db:5432/kukai"
    assert (
        settings.scheduler_sync_db_url
        == "postgresql+psycopg://user:pass@db:5432/kukai_scheduler"
    )


def test_sync_db_url_leaves_sync_postgresql_url_unchanged():
    settings = Settings(
        bot_token="token",
        database_url="postgresql+psycopg://user:pass@db:5432/kukai",
        scheduler_db_url="postgresql+psycopg://user:pass@db:5432/kukai",
    )

    assert settings.sync_db_url == "postgresql+psycopg://user:pass@db:5432/kukai"
    assert settings.scheduler_sync_db_url == "postgresql+psycopg://user:pass@db:5432/kukai"
