"""LLM-інструменти ZZZ-режиму поверх zzz.db.ZZZDatabase.

Реєструються в глобальний реєстр llm.tools.TOOLS когом cogs/zzz.py, але їхні
схеми додаються до запиту ЛИШЕ коли канал у режимі zzz (/mode zzz) — у
звичайних каналах радник не витрачає жодного токена.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

KINDS = ("agents", "wengines", "discs", "bangboo")


def _db(tctx):
    return getattr(tctx.bot, "zzz_db", None)


_NO_DB = "База ZZZ не завантажена. Власнику бота: python -m zzz.build_db, потім /zzz_reload."


async def tool_zzz_search(tctx, query: str, kind: str | None = None) -> str:
    db = _db(tctx)
    if db is None:
        return _NO_DB
    results = db.search(str(query), kind=kind if kind in KINDS else None)
    if not results:
        return f"Нічого не знайдено за «{query}». Спробуй zzz_overview, щоб побачити всі назви."
    return "\n".join(f"{k}:{item_id} — {name}" for k, item_id, name in results[:15])


async def tool_zzz_describe(tctx, kind: str, name: str) -> str:
    db = _db(tctx)
    if db is None:
        return _NO_DB
    if kind not in KINDS:
        return f"kind має бути одним із: {', '.join(KINDS)}"
    return db.describe(kind, str(name))


async def tool_zzz_overview(tctx, kind: str) -> str:
    db = _db(tctx)
    if db is None:
        return _NO_DB
    if kind not in KINDS:
        return f"kind має бути одним із: {', '.join(KINDS)}"
    return db.overview(kind)


async def tool_zzz_bangboo_match(tctx, team: list) -> str:
    db = _db(tctx)
    if db is None:
        return _NO_DB
    if not isinstance(team, list) or not team:
        return "team — непорожній список імен агентів команди (2–4)."
    return db.match_bangboo([str(name) for name in team][:4])


ZZZ_TOOLS = {
    "zzz_search": tool_zzz_search,
    "zzz_describe": tool_zzz_describe,
    "zzz_overview": tool_zzz_overview,
    "zzz_bangboo_match": tool_zzz_bangboo_match,
}

_KIND_SCHEMA = {
    "type": "string",
    "enum": list(KINDS),
    "description": "Тип запису: agents / wengines / discs / bangboo",
}

ZZZ_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "zzz_search",
            "description": (
                "Пошук у локальній базі Zenless Zone Zero за назвою (англійською або китайською). "
                "Повертає збіги у форматі kind:id — назва."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Назва або її частина"},
                    "kind": {**_KIND_SCHEMA, "description": "Опційно звузити тип запису"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "zzz_describe",
            "description": (
                "Повна довідка про запис: агент (кит, статки, майндскейпи, рекомендація гри, "
                "нотатки власника), W-Engine, Drive Disc або банбу. Клич перед порадами по білдах."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": _KIND_SCHEMA,
                    "name": {"type": "string", "description": "Назва або id запису"},
                },
                "required": ["kind", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "zzz_overview",
            "description": "Повний перелік записів типу (назви, рідкість) — щоб побачити, що існує в грі.",
            "parameters": {
                "type": "object",
                "properties": {"kind": _KIND_SCHEMA},
                "required": ["kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "zzz_bangboo_match",
            "description": (
                "Підбір банбу під склад команди: збирає профіль команди (елементи, фракції, "
                "спеціальності) і перевіряє умови активації кожного банбу. Клич, коли питають, "
                "якого банбу ставити з певними агентами."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Імена агентів команди (2–4)",
                    }
                },
                "required": ["team"],
            },
        },
    },
]
