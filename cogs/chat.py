"""LLM-чат: реагує на @згадку, реплай на повідомлення бота та DM.

Оформлення відповідей (кожне вимикається у .env):
- EMBED_REPLIES  — відповіді в ембедах (колір за режимом каналу, до 4000 симв./блок)
- FOOTER_STATS   — футер: викликані тули + витрачені токени запиту
- REPLY_BUTTONS  — кнопки 🔁 «Переролити» (лише автор запиту) і 🧹 «Забути розмову»
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from llm.memory import build_messages, estimate_tokens
from llm.tools import BASE_TOOL_SCHEMAS, TOOL_SCHEMAS, AgentResult, ToolContext, run_agent
from utils import fix_tables, looks_ukrainian, split_message
from zzz.tools import ZZZ_MODE_PROMPT, ZZZ_TOOL_SCHEMAS

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
COLOR_NORMAL = discord.Color.blurple()
COLOR_ZZZ = discord.Color.from_str("#A4DE02")
EMBED_LIMIT = 4000
PLAIN_LIMIT = 1900
FOOTER_FIT_LIMIT = 1990  # футер тулиться в останній чанк, лише якщо влазить у ліміт Discord
VIEW_TIMEOUT = 300  # секунд до самознищення кнопок


def build_footer(result: AgentResult) -> str:
    """Компактний підпис: тули + токени + кількість викликів LLM."""
    parts: list[str] = []
    if result.tool_calls:
        shown = " · ".join(result.tool_calls[:4])
        extra = len(result.tool_calls) - 4
        if extra > 0:
            shown += f" +{extra}"
        parts.append(f"🔧 {shown}")
    parts.append(f"🎫 {result.prompt_tokens / 1000:.1f}k → {result.completion_tokens / 1000:.1f}k токенів")
    if result.llm_calls > 1:
        parts.append(f"⛓ {result.llm_calls} виклики LLM")
    return " │ ".join(parts)


def build_embeds(chunks: list[str], *, zzz: bool, footer: str | None) -> list[discord.Embed]:
    """Список ембедів: колір за режимом, бейдж ZZZ на першому, футер на останньому."""
    color = COLOR_ZZZ if zzz else COLOR_NORMAL
    embeds: list[discord.Embed] = []
    for index, chunk in enumerate(chunks):
        embed = discord.Embed(description=chunk, color=color)
        if zzz and index == 0:
            embed.set_author(name="⚡ ZZZ-радник")
        if footer and index == len(chunks) - 1:
            embed.set_footer(text=footer[:2048])
        embeds.append(embed)
    return embeds


class ReplyView(discord.ui.View):
    """Кнопки під останнім повідомленням відповіді."""

    def __init__(self, cog: "ChatCog", user_message: discord.Message):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.user_message = user_message
        self.message: discord.Message | None = None  # виставляється після відправки

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(emoji="🔁", label="Переролити", style=discord.ButtonStyle.secondary)
    async def reroll_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_message.author.id:
            await interaction.response.send_message(
                "⛔ Переролити може лише автор запиту (це його токени горять).", ephemeral=True
            )
            return
        await interaction.response.defer()
        self.stop()
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass
        await self.cog.reroll(self.user_message)

    @discord.ui.button(emoji="🧹", label="Забути розмову", style=discord.ButtonStyle.secondary)
    async def forget_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        deleted = await self.cog._clear_history(interaction.channel_id, interaction.guild_id)
        await interaction.response.send_message(
            f"🧹 Пам'ять цього каналу очищено ({deleted} повідомлень).", ephemeral=True
        )


class ChatCog(commands.Cog, name="Чат"):
    # Позначка, що лишається першою в історії після чистки: бот завжди знає,
    # що була амнезія, і не дає дописувати собі минуле, якого не існує.
    RESET_MARKER = (
        "(системная отметка: память этого канала только что очищена владельцем. "
        "Более ранних сообщений у тебя НЕТ — если собеседники ссылаются на «вчера» "
        "или «ты говорил», не подтверждай и не выдумывай: этой памяти не существует.)"
    )

    def __init__(self, bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _clear_history(self, channel_id: int, guild_id: int | None) -> int:
        """Чистка пам'яті каналу + надгробок-позначка."""
        deleted = await self.bot.db.clear_chat_history(channel_id)
        await self.bot.db.add_chat_message(channel_id, guild_id, "user", self.RESET_MARKER)
        return deleted

    # ---------------- тригери ----------------

    async def _is_trigger(self, message: discord.Message) -> bool:
        if message.guild is None:
            return True  # у DM відповідаємо на все

        if any(user.id == self.bot.user.id for user in message.mentions):
            return True

        ref = message.reference
        if ref and ref.message_id:
            resolved = ref.resolved
            if resolved is None:
                try:
                    resolved = await message.channel.fetch_message(ref.message_id)
                except discord.HTTPException:
                    return False
            return isinstance(resolved, discord.Message) and resolved.author.id == self.bot.user.id
        return False

    def _clean_content(self, message: discord.Message) -> str:
        content = message.content or ""
        for pattern in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
            content = content.replace(pattern, "")
        return content.strip()

    # ---------------- основний обробник ----------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if self.bot.user is None or message.author.bot:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if not await self._is_trigger(message):
            return

        content = self._clean_content(message)
        if message.attachments:
            names = ", ".join(a.filename for a in message.attachments)
            content = f"{content}\n(долучені файли, їх вміст мені недоступний: {names})".strip()

        if not content:
            await message.reply(
                "Я тут 🐾 Напиши питання після згадки або відповідай реплаєм на мої повідомлення.",
                mention_author=False,
            )
            return

        lock = self._locks[message.channel.id]
        if lock.locked():
            try:
                await message.add_reaction("⏳")
            except discord.HTTPException:
                pass

        async with lock:
            try:
                async with message.channel.typing():
                    guild_id = message.guild.id if message.guild else None
                    await self.bot.db.add_chat_message(
                        message.channel.id, guild_id, "user",
                        f"{message.author.display_name}: {content}",
                    )
                    result, zzz_mode = await self._run(message)
            except Exception as exc:  # noqa: BLE001 — користувачу коротко, деталі в лог
                log.exception("Помилка генерації відповіді")
                await message.reply(
                    f"⚠️ Не вдалося отримати відповідь: {type(exc).__name__}. Деталі — у лог-файлі.",
                    mention_author=False,
                )
                return

        await self._send_reply(message, result, zzz_mode)

    async def _run(self, message: discord.Message) -> tuple[AgentResult, bool]:
        """Генерація з наявної історії каналу (репліка користувача вже в БД)."""
        db = self.bot.db
        cfg = self.bot.config
        rows = await db.get_chat_history(message.channel.id)

        system_suffix, schemas, zzz_mode, auto_labels = await self._resolve_mode(message, rows)

        messages = build_messages(
            cfg,
            rows,
            bot_name=self.bot.user.display_name,
            guild_name=message.guild.name if message.guild else None,
            channel_name=getattr(message.channel, "name", None),
            system_suffix=system_suffix,
        )
        tctx = ToolContext(bot=self.bot, message=message, db=db)
        result = await run_agent(self.bot.llm, messages, tctx, cfg.max_tool_iterations, schemas=schemas)
        result.text = fix_tables(result.text)

        if cfg.lang_guard == "ru" and looks_ukrainian(result.text):
            await self._apply_lang_guard(result, messages, tctx, schemas)

        if auto_labels:
            result.tool_calls[:0] = [f"📦 {label}" for label in auto_labels]

        guild_id = message.guild.id if message.guild else None
        await db.add_chat_message(message.channel.id, guild_id, "assistant", result.text)
        return result, zzz_mode

    async def _resolve_mode(
        self, message: discord.Message, rows: list[dict]
    ) -> tuple[str, list, bool, list[str]]:
        """Режим каналу: системний доважок, набір тулів, прапорець ZZZ і мітки
        авто-контексту (детермінована підкладка даних по сутностях повідомлення)."""
        cfg = self.bot.config
        schemas = list(TOOL_SCHEMAS) if cfg.web_tools else list(BASE_TOOL_SCHEMAS)
        zzz_db = getattr(self.bot, "zzz_db", None)
        zzz_mode = zzz_db is not None and await self.bot.db.get_channel_mode(message.channel.id) == "zzz"
        if not zzz_mode:
            return "", schemas, False, []

        system_suffix = ZZZ_MODE_PROMPT.format(version=zzz_db.meta.get("game_version", "?"))
        schemas = schemas + ZZZ_TOOL_SCHEMAS
        last_user = next((row["content"] for row in reversed(rows) if row["role"] == "user"), "")
        auto_block, auto_labels = zzz_db.auto_context(last_user)
        if auto_block:
            system_suffix = f"{system_suffix}\n\n{auto_block}"
        return system_suffix, schemas, True, auto_labels

    async def _apply_lang_guard(
        self, result: AgentResult, messages: list[dict], tctx: ToolContext, schemas: list
    ) -> None:
        """Коригувальний ретрай, коли LongCat дзеркалить українську попри персону.
        Мутує result: один ретрай, і лише вдалий (не-український) замінює текст."""
        log.info("Мовний страж: відповідь українською, роблю коригувальний ретрай")
        messages.append({"role": "assistant", "content": result.text})
        messages.append(
            {
                "role": "user",
                "content": (
                    "(система: ответ выше написан по-украински — это нарушает правило языка "
                    "персоны. Перепиши его по-русски, сохранив содержание, тон и ремарку. "
                    "Выведи ТОЛЬКО переписанный ответ.)"
                ),
            }
        )
        retry = await run_agent(self.bot.llm, messages, tctx, 1, schemas=schemas)
        result.prompt_tokens += retry.prompt_tokens
        result.completion_tokens += retry.completion_tokens
        result.llm_calls += retry.llm_calls
        candidate = fix_tables(retry.text)
        if candidate and not looks_ukrainian(candidate):
            result.text = candidate
            result.tool_calls.append("🌐 мовний ретрай")

    async def reroll(self, user_message: discord.Message) -> None:
        """🔁: прибрати останню відповідь з пам'яті і згенерувати заново."""
        await self.bot.db.delete_last_assistant(user_message.channel.id)
        lock = self._locks[user_message.channel.id]
        async with lock:
            try:
                async with user_message.channel.typing():
                    result, zzz_mode = await self._run(user_message)
            except Exception as exc:  # noqa: BLE001
                log.exception("Перерол впав")
                try:
                    await user_message.channel.send(
                        f"⚠️ Переролити не вдалося: {type(exc).__name__}. Деталі — у лог-файлі."
                    )
                except discord.HTTPException:
                    pass
                return
        await self._send_reply(user_message, result, zzz_mode)

    # ---------------- відправка ----------------

    async def _send_reply(self, message: discord.Message, result: AgentResult, zzz_mode: bool) -> None:
        cfg = self.bot.config
        footer = build_footer(result) if cfg.footer_stats else None
        view = ReplyView(self, message) if cfg.reply_buttons else None

        payloads = self._build_payloads(result.text, zzz_mode, footer, embed=cfg.embed_replies)
        last_sent: discord.Message | None = None
        for index, payload in enumerate(payloads):
            kwargs = {"allowed_mentions": NO_PINGS, **payload}
            if view is not None and index == len(payloads) - 1:
                kwargs["view"] = view
            if index == 0:
                try:
                    last_sent = await message.reply(mention_author=False, **kwargs)
                except discord.HTTPException:
                    last_sent = await message.channel.send(**kwargs)
            else:
                last_sent = await message.channel.send(**kwargs)

        if view is not None:
            view.message = last_sent

    def _build_payloads(
        self, text: str, zzz_mode: bool, footer: str | None, *, embed: bool
    ) -> list[dict]:
        """Готує список kwargs (embed=... або content=...) — по одному на повідомлення."""
        if embed:
            chunks = split_message(text, limit=EMBED_LIMIT)
            embeds = build_embeds(chunks, zzz=zzz_mode, footer=footer)
            return [{"embed": e} for e in embeds]

        chunks = split_message(text, limit=PLAIN_LIMIT)
        if footer:
            footer_line = f"\n-# {footer}"
            if len(chunks[-1]) + len(footer_line) < FOOTER_FIT_LIMIT:
                chunks[-1] += footer_line
            else:
                chunks.append(f"-# {footer}")
        return [{"content": c} for c in chunks]

    # ---------------- слеш-команди ----------------

    @app_commands.command(name="reset", description="Очистити пам'ять розмови в цьому каналі")
    async def reset(self, interaction: discord.Interaction):
        deleted = await self._clear_history(interaction.channel_id, interaction.guild_id)
        await interaction.response.send_message(
            f"🧹 Пам'ять цього каналу очищено ({deleted} повідомлень)."
        )

    @app_commands.command(name="context", description="Скільки пам'яті розмови накопичено в цьому каналі")
    async def context(self, interaction: discord.Interaction):
        rows = await self.bot.db.get_chat_history(interaction.channel_id)
        tokens = sum(estimate_tokens(row["content"]) for row in rows)
        limit = self.bot.config.history_token_limit
        await interaction.response.send_message(
            f"💾 Повідомлень у пам'яті: {len(rows)} · ≈{tokens} токенів (у кожен запит іде до {limit}).",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(ChatCog(bot))
