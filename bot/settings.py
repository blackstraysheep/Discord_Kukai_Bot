from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _to_sync_db_url(url: str) -> str:
    """Convert known SQLAlchemy async driver URLs to sync-driver URLs."""
    replacements = {
        "sqlite+aiosqlite://": "sqlite://",
        "postgresql+asyncpg://": "postgresql+psycopg://",
    }
    for async_prefix, sync_prefix in replacements.items():
        if url.startswith(async_prefix):
            return url.replace(async_prefix, sync_prefix, 1)
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./data/kukai.db"
    scheduler_db_url: str = "sqlite:///./data/kukai_scheduler.db"
    data_dir: str = "./data"
    log_level: str = "INFO"
    # Comma-separated guild IDs for fast command sync during development
    dev_guild_ids: str = ""

    @property
    def dev_guild_id_list(self) -> list[int]:
        if not self.dev_guild_ids:
            return []
        return [int(g.strip()) for g in self.dev_guild_ids.split(",") if g.strip()]

    @property
    def sync_db_url(self) -> str:
        """Synchronous DB URL used by tools that cannot use async drivers."""
        return _to_sync_db_url(self.database_url)

    @property
    def scheduler_sync_db_url(self) -> str:
        """Synchronous DB URL used by APScheduler SQLAlchemyJobStore."""
        return _to_sync_db_url(self.scheduler_db_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
