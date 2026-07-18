"""Нативні опитування Discord (/poll) — голоси й результати рахує сам Discord."""
from __future__ import annotations

import datetime

import discord
from discord import app_commands
from discord.ext import commands


class PollsCog(commands.Cog, name="Опитування"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="poll", description="Створити опитування")
    @app_commands.describe(
        question="Питання (до 300 символів)",
        options="Варіанти через ; або , (від 2 до 10, до 55 символів кожен)",
        hours="Тривалість у годинах (1–768, типово 24)",
        multiple="Дозволити обирати кілька відповідей",
    )
    async def poll(
        self,
        interaction: discord.Interaction,
        question: str,
        options: str,
        hours: app_commands.Range[int, 1, 768] = 24,
        multiple: bool = False,
    ):
        if interaction.channel is None:
            await interaction.response.send_message("⛔ Тут немає каналу для опитування.", ephemeral=True)
            return

        separator = ";" if ";" in options else ","
        items = [item.strip()[:55] for item in options.split(separator) if item.strip()]
        if not 2 <= len(items) <= 10:
            await interaction.response.send_message(
                "⛔ Потрібно від 2 до 10 варіантів, розділяй `;` або `,`.", ephemeral=True
            )
            return

        poll = discord.Poll(
            question=question[:300],
            duration=datetime.timedelta(hours=hours),
            multiple=multiple,
        )
        for item in items:
            poll.add_answer(text=item)

        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.channel.send(poll=poll)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"⚠️ Discord відхилив опитування: {exc}", ephemeral=True)
            return
        await interaction.followup.send("📊 Опитування створено!", ephemeral=True)


async def setup(bot):
    await bot.add_cog(PollsCog(bot))
