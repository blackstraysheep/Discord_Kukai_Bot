from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./data/kukai.db"
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
        """Synchronous SQLite URL used by Alembic migrations."""
        return self.database_url.replace("sqlite+aiosqlite", "sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
