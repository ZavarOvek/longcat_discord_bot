"""XP-рівні за активність (класична формула MEE6: на рівень n потрібно 5n²+50n+100).

15–25 XP за повідомлення, кулдаун 60 с на користувача. Вимикається через
LEVELS_ENABLED=false у .env (ког тоді просто не завантажується).
"""
from __future__ import annotations

import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

XP_COOLDOWN_SECONDS = 60
XP_MIN, XP_MAX = 15, 25
# Кожні N дотиків чистимо протухлі кулдауни, щоб словник не ріс безмежно
# на великих серверах (ключі активних юзерів перезаписуються, неактивних — ні).
COOLDOWN_SWEEP_EVERY = 500


def xp_needed(level: int) -> int:
    """Скільки XP треба, щоб перейти з рівня `level` на наступний."""
    return 5 * level * level + 50 * level + 100


def level_from_xp(total_xp: int) -> tuple[int, int, int]:
    """(рівень, прогрес у поточному рівні, скільки треба до наступного)."""
    level = 0
    remaining = total_xp
    while remaining >= xp_needed(level):
        remaining -= xp_needed(level)
        level += 1
    return level, remaining, xp_needed(level)


class LevelsCog(commands.Cog, name="Рівні"):
    def __init__(self, bot):
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}
        self._touches = 0

    def _sweep_cooldowns(self, now: float) -> None:
        """Викинути кулдауни, що вже сплили (їх однаково перевіряли б заново)."""
        stale = [key for key, ts in self._cooldowns.items() if now - ts >= XP_COOLDOWN_SECONDS]
        for key in stale:
            del self._cooldowns[key]

    def _touch_cooldown(self, key: tuple[int, int], now: float) -> None:
        self._cooldowns[key] = now
        self._touches += 1
        if self._touches >= COOLDOWN_SWEEP_EVERY:
            self._touches = 0
            self._sweep_cooldowns(now)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._cooldowns.get(key, 0.0) < XP_COOLDOWN_SECONDS:
            return
        self._touch_cooldown(key, now)

        gained = random.randint(XP_MIN, XP_MAX)
        old_total = await self.bot.db.get_xp(message.guild.id, message.author.id)
        new_total = await self.bot.db.add_xp(message.guild.id, message.author.id, gained)

        if level_from_xp(new_total)[0] > level_from_xp(old_total)[0]:
            try:
                await message.channel.send(
                    f"🎉 {message.author.mention} досягає **{level_from_xp(new_total)[0]} рівня**!"
                )
            except discord.HTTPException:
                pass

    @app_commands.command(name="rank", description="Рівень і XP користувача")
    @app_commands.describe(member="Чий ранг (порожньо = твій)")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        total = await self.bot.db.get_xp(interaction.guild_id, member.id)
        level, current, needed = level_from_xp(total)
        filled = int(10 * current / needed)
        bar = "▰" * filled + "▱" * (10 - filled)

        embed = discord.Embed(title=f"🏆 {member.display_name}", color=discord.Color.gold())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Рівень", value=str(level))
        embed.add_field(name="Всього XP", value=str(total))
        embed.add_field(name="До наступного рівня", value=f"{bar} {current}/{needed}", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Топ-10 за XP на сервері")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        rows = await self.bot.db.top_xp(interaction.guild_id, limit=10)
        if not rows:
            await interaction.response.send_message("Поки що порожньо — попишіть у чаті 🙂")
            return
        medals = ("🥇", "🥈", "🥉")
        lines = []
        for i, row in enumerate(rows):
            prefix = medals[i] if i < 3 else f"**{i + 1}.**"
            level = level_from_xp(row["xp"])[0]
            lines.append(f"{prefix} <@{row['user_id']}> — рівень {level}, {row['xp']} XP")
        embed = discord.Embed(
            title="🏆 Таблиця лідерів", description="\n".join(lines), color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(LevelsCog(bot))
