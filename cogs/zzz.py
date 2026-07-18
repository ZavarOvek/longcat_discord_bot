"""ZZZ-режим: /mode перемикає канал у радника по Zenless Zone Zero.

Режим зберігається в SQLite (переживає рестарт). У zzz-каналах до запиту
LLM додаються три інструменти і промпт-доважок з F2P-правилами; у звичайних
каналах нічого з цього не витрачає токени.
"""
from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from llm.tools import TOOLS
from zzz.db import ZZZDatabase
from zzz.tools import ZZZ_TOOLS

log = logging.getLogger(__name__)

# Де шукати згенеровані бази: типовий корінь + запуск генератора з папки zzz/
DATA_ROOTS = ("data/zzz", "zzz/data/zzz")


class ZZZCog(commands.Cog, name="ZZZ"):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        TOOLS.update(ZZZ_TOOLS)  # реєструємо виконання; схеми підмикає chat.py лише в zzz-каналах
        self._load_db()

    def _load_db(self) -> str | None:
        """Повертає текст помилки або None при успіху."""
        last_error = "бази не знайдені"
        for root in DATA_ROOTS:
            if not Path(root).exists():
                continue
            try:
                self.bot.zzz_db = ZZZDatabase(root).load()
                log.info("ZZZ: %s (з %s)", self.bot.zzz_db.stats_line(), root)
                return None
            except FileNotFoundError as exc:
                last_error = str(exc)
        self.bot.zzz_db = None
        log.warning("ZZZ БД не завантажена: %s", last_error)
        return last_error

    @app_commands.command(name="mode", description="Режим чату в цьому каналі")
    @app_commands.describe(mode="normal — звичайний чат · zzz — радник по Zenless Zone Zero")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="normal", value="normal"),
            app_commands.Choice(name="zzz", value="zzz"),
        ]
    )
    async def mode(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        if mode.value == "zzz":
            if getattr(self.bot, "zzz_db", None) is None:
                await interaction.response.send_message(
                    "⛔ ZZZ-бази не згенеровані. Власнику бота: `python -m zzz.build_db` "
                    "з кореня проєкту, потім `/zzz_reload`.",
                    ephemeral=True,
                )
                return
            await self.bot.db.set_channel_mode(interaction.channel_id, "zzz")
            version = self.bot.zzz_db.meta.get("game_version", "?")
            await interaction.response.send_message(
                f"🎮 Канал перемкнено в режим **ZZZ-радника** (дані гри v{version}). "
                f"Згадай мене і питай про агентів, білди, W-Engine чи диски. "
                f"Повернути звичайний чат: `/mode normal`."
            )
        else:
            await self.bot.db.set_channel_mode(interaction.channel_id, None)
            await interaction.response.send_message("💬 Канал у звичайному режимі чату.")

    @app_commands.command(name="zzz_reload", description="Перечитати ZZZ-бази з диска (після перегенерації)")
    async def zzz_reload(self, interaction: discord.Interaction):
        error = self._load_db()
        if error:
            await interaction.response.send_message(f"⛔ {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"🔄 {self.bot.zzz_db.stats_line()}")


async def setup(bot):
    await bot.add_cog(ZZZCog(bot))
