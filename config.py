"""Завантаження конфігурації з .env з валідацією обов'язкових полів."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "так")


def _opt_bool(value: str | None) -> bool | None:
    """true/false або None, якщо порожньо (параметр не надсилається взагалі)."""
    if value is None or value.strip() == "":
        return None
    return _bool(value)


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _ids(value: str | None) -> list[int]:
    out: list[int] = []
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


@dataclass(slots=True)
class Config:
    # обов'язкові
    discord_token: str
    longcat_api_key: str
    # LLM
    longcat_base_url: str
    longcat_model: str
    max_tokens: int
    temperature: float
    thinking: bool | None
    history_token_limit: int
    max_tool_iterations: int
    llm_concurrency: int
    system_prompt: str
    # Discord
    guild_ids: list[int]
    welcome_channel_id: int | None
    levels_enabled: bool
    # оформлення відповідей
    embed_replies: bool
    footer_stats: bool
    reply_buttons: bool
    # веб-тули (wiki + web_search) для LLM
    web_tools: bool
    # мовний вартовий: "ru" = ретраїти повністю українські відповіді, "" = вимкнено
    lang_guard: str
    # інше
    database_path: str
    log_level: str
    log_file: str


def load_config() -> Config:
    load_dotenv()

    welcome_raw = os.getenv("WELCOME_CHANNEL_ID", "").strip()

    cfg = Config(
        discord_token=os.getenv("DISCORD_TOKEN", "").strip(),
        longcat_api_key=os.getenv("LONGCAT_API_KEY", "").strip(),
        longcat_base_url=os.getenv("LONGCAT_BASE_URL", "https://api.longcat.chat/openai/v1").strip(),
        longcat_model=os.getenv("LONGCAT_MODEL", "LongCat-2.0").strip(),
        max_tokens=_int(os.getenv("LONGCAT_MAX_TOKENS"), 2048),
        temperature=_float(os.getenv("LONGCAT_TEMPERATURE"), 0.7),
        thinking=_opt_bool(os.getenv("LONGCAT_THINKING")),
        history_token_limit=_int(os.getenv("CHAT_HISTORY_TOKEN_LIMIT"), 24000),
        max_tool_iterations=max(1, _int(os.getenv("CHAT_MAX_TOOL_ITERATIONS"), 6)),
        llm_concurrency=max(1, _int(os.getenv("LLM_MAX_CONCURRENCY"), 2)),
        system_prompt=os.getenv("CHAT_SYSTEM_PROMPT", ""),
        guild_ids=_ids(os.getenv("GUILD_IDS")),
        welcome_channel_id=int(welcome_raw) if welcome_raw.isdigit() else None,
        levels_enabled=_bool(os.getenv("LEVELS_ENABLED"), True),
        embed_replies=_bool(os.getenv("EMBED_REPLIES"), True),
        footer_stats=_bool(os.getenv("FOOTER_STATS"), True),
        reply_buttons=_bool(os.getenv("REPLY_BUTTONS"), True),
        web_tools=_bool(os.getenv("WEB_TOOLS_ENABLED"), True),
        lang_guard=os.getenv("LANG_GUARD", "").strip().lower(),
        database_path=os.getenv("DATABASE_PATH", "bot.db").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip(),
        log_file=os.getenv("LOG_FILE", "bot.log").strip(),
    )

    missing = [
        name
        for name, value in (("DISCORD_TOKEN", cfg.discord_token), ("LONGCAT_API_KEY", cfg.longcat_api_key))
        if not value
    ]
    if missing:
        print(
            f"[config] Заповни у .env: {', '.join(missing)}. "
            f"Скопіюй .env.example у .env і встав значення.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Плейсхолдер або не-ASCII у ключах = зрозуміле падіння на старті замість
    # загадкового UnicodeEncodeError у надрах httpx (HTTP-заголовки — лише ASCII).
    for name, value in (
        ("DISCORD_TOKEN", cfg.discord_token),
        ("LONGCAT_API_KEY", cfg.longcat_api_key),
    ):
        if "ВСТАВ" in value or not value.isascii():
            print(
                f"[config] {name} виглядає як плейсхолдер, а не справжній ключ — "
                f"встав реальне значення у .env",
                file=sys.stderr,
            )
            raise SystemExit(1)

    return cfg
