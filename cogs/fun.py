"""Розваги: кубики, монетка, магічна куля, випадковий вибір."""
from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

from utils import roll_dice

ANSWERS_8BALL = [
    "Безсумнівно.", "Однозначно так.", "Так.", "Схоже, що так.",
    "Можеш на це розраховувати.", "Зірки кажуть — так.", "Найімовірніше.",
    "Знаки вказують на «так».", "Відповідь туманна, спробуй ще раз.",
    "Спитай пізніше.", "Краще не казатиму зараз.", "Зосередься і спитай знову.",
    "Не розраховуй на це.", "Моя відповідь — ні.", "Мої джерела кажуть — ні.",
    "Перспективи не дуже.", "Дуже сумнівно.",
]


class FunCog(commands.Cog, name="Розваги"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="roll", description="Кинути кубики (NdM+K)")
    @app_commands.describe(formula="Формула, напр. 1d20 або 2d6+3")
    async def roll(self, interaction: discord.Interaction, formula: str = "1d20"):
        try:
            rolls, modifier, total = roll_dice(formula)
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return
        rolls_text = ", ".join(map(str, rolls)) if len(rolls) <= 25 else f"{len(rolls)} кидків"
        mod_text = f" {modifier:+d}" if modifier else ""
        await interaction.response.send_message(f"🎲 `{formula}` → [{rolls_text}]{mod_text} = **{total}**")

    @app_commands.command(name="coinflip", description="Підкинути монетку")
    async def coinflip(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🪙 **{random.choice(('Орел', 'Решка'))}**!")

    @app_commands.command(name="8ball", description="Запитати магічну кулю")
    @app_commands.describe(question="Твоє питання")
    async def eightball(self, interaction: discord.Interaction, question: str):
        await interaction.response.send_message(
            f"❓ {question[:200]}\n🎱 **{random.choice(ANSWERS_8BALL)}**"
        )

    @app_commands.command(name="choose", description="Обрати випадковий варіант зі списку")
    @app_commands.describe(options="Варіанти через кому або крапку з комою")
    async def choose(self, interaction: discord.Interaction, options: str):
        separator = ";" if ";" in options else ","
        items = [item.strip() for item in options.split(separator) if item.strip()]
        if len(items) < 2:
            await interaction.response.send_message(
                "⛔ Дай хоча б два варіанти, розділені комою.", ephemeral=True
            )
            return
        await interaction.response.send_message(f"🎯 Обираю: **{random.choice(items)}**")


async def setup(bot):
    await bot.add_cog(FunCog(bot))
