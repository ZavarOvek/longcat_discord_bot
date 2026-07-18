"""Інструменти (function calling) для LongCat + агентний цикл.

Принципи:
- інструменти лише читають дані або створюють нешкідливі речі
  (нагадування, опитування). Модераційні дії LLM недоступні — вони
  тільки у slash-командах з перевіркою прав.
- будь-яка помилка інструмента повертається моделі текстом, щоб вона
  могла виправитися, а не валила весь запит.
- результат обрізається до MAX_RESULT_CHARS, щоб не палити квоту.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote

import discord
import httpx

from utils import roll_dice

if TYPE_CHECKING:
    from database import Database

log = logging.getLogger(__name__)

MAX_RESULT_CHARS = 4000
_MENTION_RE = re.compile(r"^<@!?(\d+)>$")


@dataclass(slots=True)
class ToolContext:
    """Все, що потрібно інструментам: бот, повідомлення-тригер і БД."""
    bot: discord.Client
    message: discord.Message
    db: "Database"


# ---------------- Реалізації інструментів ----------------

async def tool_get_current_time(tctx: ToolContext) -> str:
    now = datetime.datetime.now().astimezone()
    return now.strftime("%Y-%m-%d %H:%M:%S %Z (%A)")


async def tool_get_server_info(tctx: ToolContext) -> str:
    guild = tctx.message.guild
    if guild is None:
        return "Це особисті повідомлення — сервера немає."
    lines = [
        f"Назва: {guild.name}",
        f"ID: {guild.id}",
        f"Учасників: {guild.member_count}",
        f"Створено: {guild.created_at:%Y-%m-%d}",
        f"Канали: текстових {len(guild.text_channels)}, голосових {len(guild.voice_channels)}",
        f"Ролей: {len(guild.roles)}",
        f"Бусти: {guild.premium_subscription_count} (рівень {guild.premium_tier})",
    ]
    return "\n".join(lines)


async def tool_get_user_info(tctx: ToolContext, user: str) -> str:
    guild = tctx.message.guild
    if guild is None:
        return "Це особисті повідомлення — тут немає учасників сервера."

    query = str(user).strip()
    member: discord.Member | None = None

    member_id: int | None = None
    mention = _MENTION_RE.match(query)
    if mention:
        member_id = int(mention.group(1))
    elif query.isdigit():
        member_id = int(query)

    if member_id is not None:
        member = guild.get_member(member_id)
        if member is None:
            try:
                member = await guild.fetch_member(member_id)
            except discord.HTTPException:
                member = None

    if member is None:
        needle = query.lstrip("@").lower()
        member = discord.utils.find(
            lambda m: m.display_name.lower() == needle
            or m.name.lower() == needle
            or needle in m.display_name.lower(),
            guild.members,
        )

    if member is None:
        return f"Не знайшов користувача «{user}» на цьому сервері."

    roles = ", ".join(r.name for r in reversed(member.roles[1:])) or "—"
    joined = f"{member.joined_at:%Y-%m-%d}" if member.joined_at else "невідомо"
    lines = [
        f"Ім'я на сервері: {member.display_name} (@{member.name})",
        f"ID: {member.id}",
        f"Бот: {'так' if member.bot else 'ні'}",
        f"Акаунт створено: {member.created_at:%Y-%m-%d}",
        f"Приєднався до сервера: {joined}",
        f"Ролі: {roles[:800]}",
    ]
    return "\n".join(lines)


async def tool_get_recent_messages(tctx: ToolContext, limit: int = 20) -> str:
    limit = max(1, min(int(limit), 50))
    lines: list[str] = []
    async for msg in tctx.message.channel.history(limit=limit, before=tctx.message):
        text = msg.clean_content or ("(вкладення)" if msg.attachments else "(порожньо/ембед)")
        lines.append(f"[{msg.created_at:%H:%M}] {msg.author.display_name}: {text[:200]}")
    lines.reverse()
    return "\n".join(lines) or "Історія каналу порожня."


async def tool_create_reminder(tctx: ToolContext, minutes: int, text: str) -> str:
    minutes = int(minutes)
    if not 1 <= minutes <= 86400:  # до 60 діб
        return "Помилка: minutes має бути від 1 до 86400 (60 діб)."
    remind_at = int(time.time()) + minutes * 60
    reminder_id = await tctx.db.add_reminder(
        tctx.message.author.id,
        tctx.message.channel.id,
        tctx.message.guild.id if tctx.message.guild else None,
        str(text)[:500],
        remind_at,
    )
    when = datetime.datetime.fromtimestamp(remind_at).strftime("%H:%M %d.%m.%Y")
    return (
        f"Нагадування №{reminder_id} створено, спрацює о {when} у цьому каналі. "
        f"У відповіді можеш вставити Discord-мітку часу <t:{remind_at}:R>."
    )


async def tool_create_poll(
    tctx: ToolContext,
    question: str,
    options: list,
    duration_hours: int = 24,
    multiple: bool = False,
) -> str:
    if not isinstance(options, list) or not 2 <= len(options) <= 10:
        return "Помилка: options — список із 2–10 текстових варіантів."
    duration_hours = max(1, min(int(duration_hours), 768))  # ліміт Discord — 32 доби

    poll = discord.Poll(
        question=str(question)[:300],
        duration=datetime.timedelta(hours=duration_hours),
        multiple=bool(multiple),
    )
    for option in options:
        poll.add_answer(text=str(option)[:55])

    try:
        await tctx.message.channel.send(poll=poll)
    except discord.HTTPException as exc:
        return f"Discord відхилив опитування: {exc}"
    return "Опитування опубліковано в каналі. Не дублюй його вміст у відповіді."


async def tool_roll_dice(tctx: ToolContext, formula: str = "1d20") -> str:
    try:
        rolls, modifier, total = roll_dice(formula)
    except ValueError as exc:
        return f"Помилка: {exc}"
    mod_text = f" {modifier:+d}" if modifier else ""
    return f"{formula}: кидки {rolls}{mod_text} = {total}"


# ---------------- веб-тули: ґрунт під фактами ----------------

_WIKI_LANGS = ("uk", "ru", "en")
_UA_HEADER = {"User-Agent": "LongcatDiscordBot/1.0 (personal Discord bot)"}


async def tool_wiki(tctx: ToolContext, query: str, lang: str = "uk") -> str:
    """Вікіпедія: перший збіг + вступ статті. Надійно, без ключів."""
    lang = lang if lang in _WIKI_LANGS else "uk"
    try:
        async with httpx.AsyncClient(timeout=10, headers=_UA_HEADER) as client:
            response = await client.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={"action": "opensearch", "search": str(query), "limit": 3, "format": "json"},
            )
            response.raise_for_status()
            titles = response.json()[1]
            if not titles:
                return (
                    f"У Вікіпедії ({lang}) нічого не знайдено за «{query}». "
                    f"Спробуй іншу мову (uk/ru/en) або web_search."
                )
            title = titles[0]
            summary = await client.get(
                f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title, safe='')}"
            )
            summary.raise_for_status()
            data = summary.json()
    except Exception as exc:  # noqa: BLE001 — мережа не варта падіння запиту
        return f"Вікіпедія недоступна ({type(exc).__name__}) — спробуй web_search або чесно скажи, що перевірити не вдалося."
    extract = (data.get("extract") or "").strip()[:1800]
    url = (data.get("content_urls") or {}).get("desktop", {}).get("page", "")
    lines = [f"Вікіпедія ({lang}): {data.get('title', title)}", extract or "(стаття без вступу)"]
    if url:
        lines.append(f"Джерело: {url}")
    if len(titles) > 1:
        lines.append("Інші збіги: " + ", ".join(titles[1:3]))
    return "\n".join(lines)


async def tool_web_search(tctx: ToolContext, query: str, max_results: int = 5) -> str:
    """Пошук DuckDuckGo (через ddgs): заголовок + фрагмент + лінк."""
    max_results = max(1, min(int(max_results), 8))

    def _run() -> list[dict]:
        from ddgs import DDGS  # імпорт тут: без пакета тул деградує, а не валить бота

        with DDGS() as engine:
            return list(engine.text(str(query), max_results=max_results))

    try:
        results = await asyncio.to_thread(_run)  # ddgs синхронний — не блокуємо event loop
    except Exception as exc:  # noqa: BLE001
        return (
            f"Пошук недоступний ({type(exc).__name__}) — спробуй wiki, "
            f"або чесно скажи, що перевірити не вдалося."
        )
    if not results:
        return f"Нічого не знайдено за «{query}»."
    lines = [
        f"• {item.get('title', '')} — {(item.get('body') or '')[:200]} ({item.get('href', '')})"
        for item in results
    ]
    return "\n".join(lines)[:4000]


# ---------------- Реєстр і схеми ----------------

TOOLS = {
    "get_current_time": tool_get_current_time,
    "get_server_info": tool_get_server_info,
    "get_user_info": tool_get_user_info,
    "get_recent_messages": tool_get_recent_messages,
    "create_reminder": tool_create_reminder,
    "create_poll": tool_create_poll,
    "roll_dice": tool_roll_dice,
    "wiki": tool_wiki,
    "web_search": tool_web_search,
}

BASE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Поточні дата й час на машині бота.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_server_info",
            "description": "Інформація про поточний Discord-сервер: назва, учасники, канали, ролі, бусти.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_info",
            "description": "Інформація про учасника сервера: ім'я, ролі, дата створення акаунта і приєднання.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "Ім'я, нік, @згадка або числовий ID користувача",
                    }
                },
                "required": ["user"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_messages",
            "description": (
                "Останні повідомлення поточного каналу (до поточного) — реальна історія Discord. "
                "Використовуй для контексту, якого бракує, і ОБОВ'ЯЗКОВО для суперечок про те, "
                "що було сказано раніше (у тому числі тобою): це джерело правди, а не пам'ять."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Скільки повідомлень (1–50)", "default": 20}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "Створити нагадування для автора запиту в цьому каналі.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "integer", "description": "Через скільки хвилин (1–86400)"},
                    "text": {"type": "string", "description": "Текст нагадування"},
                },
                "required": ["minutes", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_poll",
            "description": "Опублікувати нативне опитування Discord у поточному каналі.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Питання (до 300 символів)"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2–10 варіантів (до 55 символів кожен)",
                    },
                    "duration_hours": {"type": "integer", "description": "Тривалість у годинах (1–768)", "default": 24},
                    "multiple": {"type": "boolean", "description": "Дозволити кілька відповідей", "default": False},
                },
                "required": ["question", "options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Кинути кубики за формулою NdM+K, наприклад 2d6+3.",
            "parameters": {
                "type": "object",
                "properties": {
                    "formula": {"type": "string", "description": "Формула, напр. 1d20 або 2d6+3", "default": "1d20"}
                },
                "required": [],
            },
        },
    },
]

WEB_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "wiki",
            "description": (
                "Вікіпедія: довідка про людину, подію, персонажа, гру, поняття. "
                "Клич ПЕРЕД впевненими твердженнями про реальний світ чи лор, і "
                "ОБОВ'ЯЗКОВО коли співрозмовник оскаржує твій факт."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Назва статті або тема"},
                    "lang": {
                        "type": "string",
                        "enum": ["uk", "ru", "en"],
                        "description": "Мова вікі; для ігрового лору й техніки найповніша en",
                        "default": "uk",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Пошук в інтернеті (DuckDuckGo): свіжі події, речі поза Вікіпедією, "
                "перевірка існування того, у чому не впевнений."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Пошуковий запит"},
                    "max_results": {"type": "integer", "description": "1–8, типово 5", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_SCHEMAS = BASE_TOOL_SCHEMAS + WEB_TOOL_SCHEMAS


async def execute_tool(name: str, arguments: str | None, tctx: ToolContext) -> str:
    """Виконує інструмент і ЗАВЖДИ повертає рядок (помилки — текстом для моделі)."""
    fn = TOOLS.get(name)
    if fn is None:
        return f"Помилка: невідомий інструмент «{name}»."
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return "Помилка: аргументи не є валідним JSON."
    if not isinstance(args, dict):
        return "Помилка: аргументи мають бути JSON-об'єктом."
    try:
        result = await fn(tctx, **args)
    except TypeError as exc:
        return f"Помилка аргументів {name}: {exc}"
    except discord.Forbidden:
        return "Помилка: у бота немає прав на цю дію в цьому каналі."
    except Exception as exc:  # noqa: BLE001 — помилку віддаємо моделі, лог лишаємо собі
        log.exception("Інструмент %s впав", name)
        return f"Помилка виконання {name}: {type(exc).__name__}: {exc}"
    return str(result)[:MAX_RESULT_CHARS]


def _call_label(call: Any) -> str:
    """Стислий підпис виклику для футера: name(перший_аргумент)."""
    name = call.function.name
    try:
        args = json.loads(call.function.arguments or "{}")
        first = next(iter(args.values()), None)
        if isinstance(first, str) and first:
            return f"{name}({first[:24]})"
        if isinstance(first, list) and first:
            suffix = "…" if len(first) > 1 else ""
            return f"{name}({str(first[0])[:24]}{suffix})"
    except Exception:  # noqa: BLE001 — підпис не вартий падіння
        pass
    return name


@dataclass(slots=True)
class AgentResult:
    """Підсумок агентного циклу: текст + статистика для футера."""

    text: str = ""
    tool_calls: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0


async def run_agent(
    llm,
    messages: list[dict],
    tctx: ToolContext,
    max_iterations: int,
    schemas: list[dict] | None = None,
) -> AgentResult:
    """Агентний цикл: модель ↔ інструменти, доки не буде текстової відповіді.
    schemas — набір схем для цього запиту (типово базові TOOL_SCHEMAS; режими
    можуть передавати розширений). На останній дозволеній ітерації інструменти
    не передаються — модель змушена відповісти текстом.
    Повертає AgentResult з текстом і статистикою (тули, токени, виклики)."""
    schemas = schemas or TOOL_SCHEMAS
    stats = AgentResult()
    for iteration in range(max_iterations):
        is_last = iteration == max_iterations - 1
        chat_result = await llm.chat(messages, tools=None if is_last else schemas)
        message = chat_result.message
        stats.llm_calls += 1
        stats.prompt_tokens += chat_result.prompt_tokens
        stats.completion_tokens += chat_result.completion_tokens

        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            stats.text = (message.content or "").strip() or "🤔 (модель повернула порожню відповідь)"
            return stats

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments or "{}",
                        },
                    }
                    for call in tool_calls
                ],
            }
        )
        log.info("Tool calls: %s", [call.function.name for call in tool_calls])

        for call in tool_calls:
            stats.tool_calls.append(_call_label(call))
            result = await execute_tool(call.function.name, call.function.arguments, tctx)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    stats.text = "⚠️ Досягнуто ліміт кроків інструментів — спробуй переформулювати запит."
    return stats
