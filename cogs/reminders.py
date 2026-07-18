"""Нагадування: зберігаються в SQLite і переживають рестарт бота.

Фоновий tasks.loop кожні 20 с забирає з БД нагадування, час яких настав.
Той самий механізм використовує LLM-інструмент create_reminder — він просто
пише в ту саму таблицю.
"""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import parse_duration

log = logging.getLogger(__name__)

MIN_REMIND_SECONDS = 10
MAX_REMIND_SECONDS = 60 * 86400  # 60 діб


class RemindersCog(commands.Cog, name="Нагадування"):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.check_loop.start()

    async def cog_unload(self):
        self.check_loop.cancel()

    @tasks.loop(seconds=20)
    async def check_loop(self):
        try:
            due = await self.bot.db.due_reminders(int(time.time()))
        except Exception:
            log.exception("Не вдалося прочитати нагадування з БД")
            return
        for row in due:
            try:
                channel = self.bot.get_channel(row["channel_id"])
                if channel is None:
                    channel = await self.bot.fetch_channel(row["channel_id"])
                await channel.send(
                    f"⏰ <@{row['user_id']}>, нагадування: {row['text']}",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException as exc:
                log.warning("Нагадування №%s не доставлено: %s", row["id"], exc)
            finally:
                await self.bot.db.complete_reminder(row["id"])

    @check_loop.before_loop
    async def before_check_loop(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="remind", description="Нагадати через певний час у цьому каналі")
    @app_commands.describe(
        when="Через скільки: 10m, 2h, 1d, 1h30m (або число — хвилини)",
        text="Про що нагадати",
    )
    async def remind(self, interaction: discord.Interaction, when: str, text: str):
        seconds = parse_duration(when)
        if not seconds or not MIN_REMIND_SECONDS <= seconds <= MAX_REMIND_SECONDS:
            await interaction.response.send_message(
                "⛔ Час: від 10 секунд до 60 діб, наприклад `10m`, `2h`, `1d`, `1h30m`.",
                ephemeral=True,
            )
            return
        remind_at = int(time.time()) + seconds
        reminder_id = await self.bot.db.add_reminder(
            interaction.user.id, interaction.channel_id, interaction.guild_id, text[:500], remind_at
        )
        await interaction.response.send_message(f"⏰ Ок, нагадаю <t:{remind_at}:R> (№{reminder_id}).")

    @app_commands.command(name="reminders", description="Мої активні нагадування")
    async def reminders(self, interaction: discord.Interaction):
        rows = await self.bot.db.user_reminders(interaction.user.id)
        if not rows:
            await interaction.response.send_message("Активних нагадувань немає.", ephemeral=True)
            return
        lines = [
            f"**№{row['id']}** — <t:{row['remind_at']}:R>: {row['text'][:80]}" for row in rows[:15]
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="reminder_delete", description="Видалити своє нагадування за номером")
    @app_commands.describe(number="Номер нагадування (див. /reminders)")
    async def reminder_delete(self, interaction: discord.Interaction, number: int):
        deleted = await self.bot.db.delete_reminder(number, interaction.user.id)
        if deleted:
            await interaction.response.send_message(f"🗑 Нагадування №{number} видалено.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "⛔ Серед твоїх активних нагадувань такого номера немає.", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(RemindersCog(bot))
