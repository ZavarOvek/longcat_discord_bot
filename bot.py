"""Точка входу LongCat Discord-бота.

Запуск: python bot.py (з активованим .venv і заповненим .env).
"""
from __future__ import annotations

import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands

from config import Config, load_config
from database import Database
from llm.client import LongcatClient

log = logging.getLogger(__name__)

EXTENSIONS = [
    "cogs.chat",
    "cogs.utility",
    "cogs.moderation",
    "cogs.fun",
    "cogs.polls",
    "cogs.reminders",
    "cogs.welcome",
    "cogs.zzz",
]


def setup_logging(cfg: Config) -> None:
    # Windows + cp1251: щоб юнікод у консолі не валив бота (перевірено Длиннокотом),
    # переводимо stdout/stderr в utf-8 з errors="replace"; повний лог — у файлі.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))

    file_handler = logging.FileHandler(cfg.log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    for noisy in ("httpx", "httpcore", "openai", "aiosqlite", "discord.gateway", "discord.http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("discord").setLevel(logging.INFO)


class LongcatBot(commands.Bot):
    def __init__(self, cfg: Config):
        intents = discord.Intents.default()
        intents.message_content = True  # privileged: текст повідомлень
        intents.members = True          # privileged: on_member_join, пошук учасників

        super().__init__(
            command_prefix="§longcat§",  # префікс-команд немає, бот повністю на slash
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=False
            ),
            activity=discord.Activity(type=discord.ActivityType.listening, name="@згадку · /help"),
        )
        self.config = cfg
        self.db = Database(cfg.database_path)
        self.llm = LongcatClient(cfg)
        self.tree.error(self.on_app_command_error)

    async def setup_hook(self) -> None:
        await self.db.init()
        log.info("База даних: %s", self.config.database_path)

        extensions = list(EXTENSIONS)
        if self.config.levels_enabled:
            extensions.append("cogs.levels")
        for extension in extensions:
            await self.load_extension(extension)
            log.info("Завантажено %s", extension)

        if self.config.guild_ids:
            for guild_id in self.config.guild_ids:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Slash-команди (%d) синхронізовано на сервер %s", len(synced), guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Глобальний sync %d команд — можуть з'являтися до 1 години", len(synced))

    async def on_ready(self) -> None:
        log.info("Увійшов як %s (ID %s), серверів: %d", self.user, self.user.id, len(self.guilds))

    async def on_command_error(self, ctx, error) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        log.error("Prefix command error", exc_info=error)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "⛔ У тебе недостатньо прав для цієї команди."
        elif isinstance(error, app_commands.BotMissingPermissions):
            message = f"⛔ Мені бракує прав: {', '.join(error.missing_permissions)}."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "⛔ Ця команда працює лише на сервері."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"⌛ Зачекай {error.retry_after:.0f} с."
        elif isinstance(error, app_commands.CheckFailure):
            message = "⛔ Умови виконання команди не виконані."
        else:
            log.error("App command error", exc_info=error)
            original = getattr(error, "original", error)
            message = f"⚠️ Помилка: {type(original).__name__}. Деталі — у лог-файлі."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    async def close(self) -> None:
        await self.db.close()
        await super().close()


def main() -> None:
    cfg = load_config()
    setup_logging(cfg)
    bot = LongcatBot(cfg)
    try:
        bot.run(cfg.discord_token, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        log.critical(
            "Увімкни privileged intents: Developer Portal → твій застосунок → Bot → "
            "MESSAGE CONTENT INTENT і SERVER MEMBERS INTENT → Save Changes."
        )
    except discord.LoginFailure:
        log.critical("Невірний DISCORD_TOKEN — перевір .env.")


if __name__ == "__main__":
    main()
