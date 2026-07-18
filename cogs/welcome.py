"""Привітання новачків. Вмикається, коли в .env задано WELCOME_CHANNEL_ID.

Потребує увімкненого SERVER MEMBERS INTENT у Developer Portal.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


class WelcomeCog(commands.Cog, name="Привітання"):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel_id = self.bot.config.welcome_channel_id
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title="👋 Вітаємо!",
            description=(
                f"{member.mention} приєднується до **{member.guild.name}**!\n"
                f"Ти учасник №{member.guild.member_count}. "
                f"Згадай мене (@{self.bot.user.display_name}) — і поговоримо 🐾"
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(content=member.mention, embed=embed)
        except discord.HTTPException as exc:
            log.warning("Не вдалося привітати %s: %s", member, exc)


async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))
