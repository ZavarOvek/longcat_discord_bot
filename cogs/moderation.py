"""Модерація: purge, timeout, kick, ban, warn, slowmode.

Кожна команда перевіряє права викликача (has_permissions), права бота
(bot_has_permissions) та ієрархію ролей (_blocked). Команди приховані
від учасників без відповідних прав (default_permissions).
"""
from __future__ import annotations

import datetime
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import parse_duration

MAX_TIMEOUT_SECONDS = 28 * 86400  # жорсткий ліміт Discord


def _blocked(invoker: discord.Member, target: discord.Member, me: discord.Member) -> Optional[str]:
    """Причина, чому дію виконати не можна, або None, якщо все гаразд."""
    if target.id == invoker.id:
        return "Не можна застосувати до себе."
    if target.id == me.id:
        return "Я відмовляюся застосовувати це до себе 😼"
    if target.id == target.guild.owner_id:
        return "Це власник сервера."
    if invoker.id != invoker.guild.owner_id and target.top_role >= invoker.top_role:
        return "У цілі роль не нижча за твою."
    if target.top_role >= me.top_role:
        return "Моя найвища роль нижча за роль цілі — підніми роль бота в Server Settings → Roles."
    return None


class ModerationCog(commands.Cog, name="Модерація"):
    def __init__(self, bot):
        self.bot = bot

    # ---------------- purge ----------------

    @app_commands.command(name="purge", description="Видалити останні N повідомлень у каналі")
    @app_commands.describe(amount="Скільки повідомлень видалити (1–100)")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    @app_commands.guild_only()
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        if not hasattr(interaction.channel, "purge"):
            await interaction.followup.send("⛔ У цьому типі каналу видаляти не можу.", ephemeral=True)
            return
        try:
            deleted = await interaction.channel.purge(limit=amount)
        except discord.HTTPException:
            await interaction.followup.send(
                "⚠️ Не все вдалося видалити — Discord не дозволяє масово видаляти "
                "повідомлення, старші за 14 днів.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(f"🧹 Видалено {len(deleted)} повідомлень.", ephemeral=True)

    # ---------------- timeout ----------------

    @app_commands.command(name="timeout", description="Відправити учасника в тайм-аут")
    @app_commands.describe(member="Кого", duration="Тривалість: 10m, 2h, 1d (макс. 28 днів)", reason="Причина")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str,
        reason: Optional[str] = None,
    ):
        seconds = parse_duration(duration)
        if not seconds or seconds > MAX_TIMEOUT_SECONDS:
            await interaction.response.send_message(
                "⛔ Тривалість: від секунд до 28 днів, наприклад `10m`, `2h`, `1d`, `1h30m`.",
                ephemeral=True,
            )
            return
        error = _blocked(interaction.user, member, interaction.guild.me)
        if error:
            await interaction.response.send_message(f"⛔ {error}", ephemeral=True)
            return
        await member.timeout(
            datetime.timedelta(seconds=seconds), reason=f"{interaction.user}: {reason or '—'}"
        )
        until = int(time.time()) + seconds
        await interaction.response.send_message(
            f"🔇 {member.mention} у тайм-ауті до <t:{until}:f>. Причина: {reason or '—'}"
        )

    @app_commands.command(name="untimeout", description="Зняти тайм-аут з учасника")
    @app_commands.describe(member="З кого зняти")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        if not member.is_timed_out():
            await interaction.response.send_message("Цей учасник і так не в тайм-ауті.", ephemeral=True)
            return
        await member.timeout(None, reason=f"Знято модератором {interaction.user}")
        await interaction.response.send_message(f"🔊 Тайм-аут із {member.mention} знято.")

    # ---------------- kick / ban ----------------

    @app_commands.command(name="kick", description="Вигнати учасника з сервера")
    @app_commands.describe(member="Кого", reason="Причина")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(
        self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None
    ):
        error = _blocked(interaction.user, member, interaction.guild.me)
        if error:
            await interaction.response.send_message(f"⛔ {error}", ephemeral=True)
            return
        await member.kick(reason=f"{interaction.user}: {reason or '—'}")
        await interaction.response.send_message(f"👢 **{member.display_name}** вигнано. Причина: {reason or '—'}")

    @app_commands.command(name="ban", description="Забанити учасника")
    @app_commands.describe(
        member="Кого",
        reason="Причина",
        delete_days="Видалити його повідомлення за останні N днів (0–7)",
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: Optional[str] = None,
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ):
        error = _blocked(interaction.user, member, interaction.guild.me)
        if error:
            await interaction.response.send_message(f"⛔ {error}", ephemeral=True)
            return
        await member.ban(
            reason=f"{interaction.user}: {reason or '—'}",
            delete_message_seconds=delete_days * 86400,
        )
        await interaction.response.send_message(f"🔨 **{member.display_name}** забанено. Причина: {reason or '—'}")

    @app_commands.command(name="unban", description="Розбанити користувача за ID")
    @app_commands.describe(user_id="Числовий ID користувача")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: str):
        user_id = user_id.strip()
        if not user_id.isdigit():
            await interaction.response.send_message("⛔ Потрібен числовий ID користувача.", ephemeral=True)
            return
        try:
            await interaction.guild.unban(discord.Object(id=int(user_id)))
        except discord.NotFound:
            await interaction.response.send_message("У бан-списку такого користувача немає.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Розбанено <@{user_id}>.")

    # ---------------- попередження ----------------

    @app_commands.command(name="warn", description="Видати попередження учаснику")
    @app_commands.describe(member="Кому", reason="Причина")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if member.bot:
            await interaction.response.send_message("⛔ Ботам попередження не видаю.", ephemeral=True)
            return
        count = await self.bot.db.add_warn(interaction.guild_id, member.id, interaction.user.id, reason)
        dm_note = ""
        try:
            await member.send(
                f"⚠️ Тобі видано попередження на сервері «{interaction.guild.name}»: {reason} (усього: {count})"
            )
        except discord.HTTPException:
            dm_note = " · в DM повідомити не вдалося"
        await interaction.response.send_message(
            f"⚠️ {member.mention} отримує попередження **№{count}**: {reason}{dm_note}"
        )

    @app_commands.command(name="warns", description="Список попереджень користувача")
    @app_commands.describe(member="Чиї попередження")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warns(self, interaction: discord.Interaction, member: discord.Member):
        rows = await self.bot.db.get_warns(interaction.guild_id, member.id)
        if not rows:
            await interaction.response.send_message(
                f"У **{member.display_name}** немає попереджень ✨", ephemeral=True
            )
            return
        lines = [
            f"**№{i + 1}** <t:{row['created_at']}:d> від <@{row['moderator_id']}>: {row['reason'] or '—'}"
            for i, row in enumerate(rows[:20])
        ]
        await interaction.response.send_message(
            f"⚠️ Попередження {member.mention} — всього {len(rows)}:\n" + "\n".join(lines),
            ephemeral=True,
        )

    @app_commands.command(name="clearwarns", description="Зняти всі попередження з користувача")
    @app_commands.describe(member="З кого зняти")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def clearwarns(self, interaction: discord.Interaction, member: discord.Member):
        removed = await self.bot.db.clear_warns(interaction.guild_id, member.id)
        await interaction.response.send_message(
            f"🧽 Знято {removed} попереджень із {member.mention}."
        )

    # ---------------- slowmode ----------------

    @app_commands.command(name="slowmode", description="Повільний режим у поточному каналі")
    @app_commands.describe(seconds="Затримка між повідомленнями, 0–21600 с (0 = вимкнути)")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        if not hasattr(interaction.channel, "edit"):
            await interaction.response.send_message("⛔ Для цього типу каналу недоступно.", ephemeral=True)
            return
        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds:
            await interaction.response.send_message(f"🐌 Слоумод: одне повідомлення раз на {seconds} с.")
        else:
            await interaction.response.send_message("🚀 Слоумод вимкнено.")


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
