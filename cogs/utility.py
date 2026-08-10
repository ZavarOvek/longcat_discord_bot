"""Утиліти: ping, serverinfo, userinfo, avatar, help."""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands


class UtilityCog(commands.Cog, name="Утиліти"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Перевірити затримку бота")
    async def ping(self, interaction: discord.Interaction):
        websocket_ms = round(self.bot.latency * 1000)
        started = time.perf_counter()
        await interaction.response.send_message("🏓 Вимірюю…")
        roundtrip_ms = round((time.perf_counter() - started) * 1000)
        await interaction.edit_original_response(
            content=f"🏓 Понг! WebSocket: **{websocket_ms} мс** · відповідь: **{roundtrip_ms} мс**"
        )

    @app_commands.command(name="serverinfo", description="Інформація про сервер")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Учасників", value=str(guild.member_count))
        embed.add_field(name="Власник", value=f"<@{guild.owner_id}>")
        embed.add_field(name="Створено", value=f"<t:{int(guild.created_at.timestamp())}:D>")
        embed.add_field(
            name="Канали", value=f"💬 {len(guild.text_channels)} · 🔊 {len(guild.voice_channels)}"
        )
        embed.add_field(name="Ролей", value=str(len(guild.roles)))
        embed.add_field(
            name="Бусти", value=f"{guild.premium_subscription_count} (рівень {guild.premium_tier})"
        )
        embed.set_footer(text=f"ID: {guild.id}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="Інформація про користувача")
    @app_commands.describe(member="Про кого (порожньо = про тебе)")
    @app_commands.guild_only()
    async def userinfo(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ):
        member = member or interaction.user
        color = member.color if member.color.value else discord.Color.blurple()
        embed = discord.Embed(title=member.display_name, color=color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Юзернейм", value=f"@{member.name}")
        embed.add_field(name="ID", value=str(member.id))
        embed.add_field(name="Бот", value="так" if member.bot else "ні")
        embed.add_field(name="Акаунт створено", value=f"<t:{int(member.created_at.timestamp())}:D>")
        if member.joined_at:
            embed.add_field(name="Приєднався", value=f"<t:{int(member.joined_at.timestamp())}:D>")
        roles = [role.mention for role in reversed(member.roles[1:])][:10]
        embed.add_field(
            name=f"Ролі ({len(member.roles) - 1})", value=" ".join(roles) or "—", inline=False
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Аватар користувача")
    @app_commands.describe(user="Чий аватар (порожньо = твій)")
    async def avatar(self, interaction: discord.Interaction, user: discord.User | None = None):
        user = user or interaction.user
        embed = discord.Embed(title=f"Аватар — {user.display_name}", color=discord.Color.blurple())
        embed.set_image(url=user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="help", description="Що вміє бот")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🐈‍⬛ Що я вмію", color=discord.Color.blurple())
        embed.add_field(
            name="🤖 Чат з LongCat",
            value=(
                "@Згадай мене або відповідай реплаєм на мої повідомлення — я тримаю контекст "
                "розмови окремо в кожному каналі (і в DM теж).\n"
                "`/reset` — забути розмову в каналі · `/context` — стан пам'яті"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠 Утиліти", value="`/ping` `/serverinfo` `/userinfo` `/avatar`", inline=False
        )
        embed.add_field(
            name="🛡 Модерація",
            value="`/purge` `/timeout` `/untimeout` `/kick` `/ban` `/unban` `/warn` `/warns` `/clearwarns` `/slowmode`",
            inline=False,
        )
        embed.add_field(
            name="🎲 Розваги", value="`/roll` `/coinflip` `/8ball` `/choose`", inline=False
        )
        embed.add_field(
            name="📊 Опитування й нагадування",
            value="`/poll` · `/remind` `/reminders` `/reminder_delete`",
            inline=False,
        )
        embed.add_field(
            name="🎮 ZZZ-радник",
            value="`/mode zzz` — перемкнути канал у радника по Zenless Zone Zero · `/zzz_reload` після оновлення баз",
            inline=False,
        )
        if self.bot.config.levels_enabled:
            embed.add_field(
                name="🏆 Рівні",
                value="`/rank` `/leaderboard` — XP за активність у чаті",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(UtilityCog(bot))
