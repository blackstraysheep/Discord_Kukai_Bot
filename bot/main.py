import logging
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit, urlunsplit

import discord
from discord.ext import commands

from bot.database import init_db
from bot.settings import get_settings

logger = logging.getLogger(__name__)

COGS = [
    "bot.cogs.kukai_cog",
    "bot.cogs.preset_cog",
    "bot.cogs.notify_preset_cog",
    "bot.cogs.entry_cog",
    "bot.cogs.submission_cog",
    "bot.cogs.select_cog",
    "bot.cogs.check_cog",
    "bot.cogs.result_cog",
    "bot.cogs.admin_cog",
    "bot.cogs.pdf_cog",
]


class KukaiBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.settings = get_settings()
        self._guild_sync_done = False

    async def setup_hook(self) -> None:
        init_db(self.settings.database_url)

        from bot.scheduler.jobs import set_bot
        from bot.scheduler.setup import init_scheduler

        scheduler = init_scheduler(self.settings.scheduler_sync_db_url)
        set_bot(self)
        scheduler.start()

        for cog in COGS:
            try:
                await self.load_extension(cog)
                logger.info("Loaded cog: %s", cog)
            except Exception:
                logger.exception("Failed to load cog: %s", cog)

        from bot.ui.persistent_views import register_persistent_views

        await register_persistent_views(self)

        # Sync slash commands: dev guilds get instant sync, global takes ~1 hour
        if self.settings.dev_guild_id_list:
            for guild_id in self.settings.dev_guild_id_list:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logger.info("Synced commands to dev guild %d", guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced commands globally")

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info("Logged in as %s (ID: %d)", self.user, self.user.id)
        if self.settings.dev_guild_id_list or self._guild_sync_done:
            return

        # Global-sync mode: clear accidental guild-scoped copies to avoid duplicates.
        cleaned = 0
        failed = 0
        for guild in self.guilds:
            try:
                self.tree.clear_commands(guild=guild)
                await self.tree.sync(guild=guild)
                cleaned += 1
            except Exception:
                failed += 1
                logger.exception("Failed to clean guild commands for guild %d", guild.id)

        self._guild_sync_done = True
        logger.info("Cleaned guild-scoped commands: success=%d failed=%d", cleaned, failed)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        from bot.utils.embed_builder import error_embed

        logger.exception("Unhandled app command error", exc_info=error)
        msg = "予期しないエラーが発生しました。"
        embed = error_embed(msg)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    logger.info(
        "event=bot_runtime_start pid=%s host=%s cwd=%s data_dir=%s database_url=%s",
        os.getpid(),
        socket.gethostname(),
        Path.cwd(),
        settings.data_dir,
        _redact_url(settings.database_url),
    )

    bot = KukaiBot()
    bot.run(settings.bot_token)


def _redact_url(raw: str) -> str:
    parsed = urlsplit(raw)
    if parsed.password is None:
        return raw
    username = parsed.username or ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    userinfo = f"{username}:***@" if username else ""
    return urlunsplit((parsed.scheme, f"{userinfo}{host}{port}", parsed.path, parsed.query, parsed.fragment))


if __name__ == "__main__":
    main()
