"""Тести llm.memory: тримінг історії, STYLE_SUFFIX завжди, TRAILING_REMINDER
лише після порогу і лише до останньої user-репліки."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm.memory import (
    STYLE_SUFFIX,
    TRAILING_REMINDER,
    TRAILING_REMINDER_THRESHOLD,
    build_messages,
    estimate_tokens,
)


def _cfg(system_prompt="", history_token_limit=24000):
    return SimpleNamespace(system_prompt=system_prompt, history_token_limit=history_token_limit)


def _rows(pairs):
    """pairs = [(role, content), ...] -> список dict-подібних рядків."""
    return [{"role": role, "content": content} for role, content in pairs]


# ---------------- estimate_tokens ----------------

def test_estimate_tokens_positive():
    assert estimate_tokens("") == 1  # мінімум 1
    assert estimate_tokens("абвгде") == 2


def test_estimate_tokens_grows_with_length():
    assert estimate_tokens("x" * 300) > estimate_tokens("x" * 30)


# ---------------- структура повідомлень ----------------

def test_first_message_is_system():
    messages = build_messages(
        _cfg(), _rows([("user", "привіт")]),
        bot_name="Бот", guild_name="Сервер", channel_name="загальний",
    )
    assert messages[0]["role"] == "system"


def test_style_suffix_always_present_default_prompt():
    messages = build_messages(
        _cfg(), _rows([("user", "привіт")]),
        bot_name="Бот", guild_name=None, channel_name=None,
    )
    assert STYLE_SUFFIX in messages[0]["content"]


def test_style_suffix_present_with_custom_persona():
    messages = build_messages(
        _cfg(system_prompt="Ти саркастичний персонаж."),
        _rows([("user", "привіт")]),
        bot_name="Бот", guild_name="С", channel_name="к",
    )
    system = messages[0]["content"]
    assert "саркастичний" in system
    assert STYLE_SUFFIX in system


def test_system_suffix_appended():
    messages = build_messages(
        _cfg(), _rows([("user", "привіт")]),
        bot_name="Бот", guild_name="С", channel_name="к",
        system_suffix="РЕЖИМ ZZZ активний.",
    )
    assert "РЕЖИМ ZZZ активний." in messages[0]["content"]


def test_dm_location_marker():
    messages = build_messages(
        _cfg(system_prompt=""),  # дефолтний промпт містить {location}
        _rows([("user", "привіт")]),
        bot_name="Бот", guild_name=None, channel_name=None,
    )
    assert "особисті повідомлення" in messages[0]["content"].lower()


def test_history_included_in_order():
    messages = build_messages(
        _cfg(), _rows([("user", "перше"), ("assistant", "друге"), ("user", "третє")]),
        bot_name="Бот", guild_name="С", channel_name="к",
    )
    contents = [m["content"] for m in messages[1:]]
    assert contents[0] == "перше"
    assert contents[-1].startswith("третє")  # може мати доважок-нагадування


# ---------------- тримінг за токен-бюджетом ----------------

def test_trimming_drops_oldest():
    # бюджет вміщує лише частину: 50 повідомлень * ~108 токенів = ~5400,
    # ліміт 3000 лишає позитивний бюджет після системного промпта, але ріже історію
    rows = _rows([("user", "x" * 300) for _ in range(50)])
    messages = build_messages(
        _cfg(history_token_limit=3000), rows,
        bot_name="Бот", guild_name="С", channel_name="к",
    )
    history = messages[1:]
    assert 0 < len(history) < 50  # частину відрізало


def test_trimming_keeps_newest():
    rows = _rows([("user", f"msg{i} " + "x" * 300) for i in range(50)])
    messages = build_messages(
        _cfg(history_token_limit=3000), rows,
        bot_name="Бот", guild_name="С", channel_name="к",
    )
    history = messages[1:]
    # останнє повідомлення історії має бути з найновіших
    assert "msg49" in history[-1]["content"]


def test_large_budget_keeps_all():
    rows = _rows([("user", f"m{i}") for i in range(20)])
    messages = build_messages(
        _cfg(history_token_limit=100000), rows,
        bot_name="Бот", guild_name="С", channel_name="к",
    )
    assert len(messages[1:]) == 20


# ---------------- TRAILING_REMINDER ----------------

def test_no_trailing_reminder_below_threshold():
    rows = _rows([("user", "коротке питання")])
    messages = build_messages(
        _cfg(), rows, bot_name="Бот", guild_name="С", channel_name="к",
    )
    assert TRAILING_REMINDER not in messages[-1]["content"]


def test_trailing_reminder_after_threshold_on_user():
    # достатньо історії, щоб перевищити поріг; остання репліка — user
    big = "x" * 300
    rows = _rows([("user" if i % 2 == 0 else "assistant", f"{big} {i}") for i in range(60)])
    assert rows[-1]["role"] != "user"
    rows.append({"role": "user", "content": "останнє питання користувача"})
    messages = build_messages(
        _cfg(history_token_limit=100000), rows,
        bot_name="Бот", guild_name="С", channel_name="к",
    )
    last = messages[-1]
    assert last["role"] == "user"
    assert TRAILING_REMINDER in last["content"]
    assert "останнє питання користувача" in last["content"]


def test_no_trailing_reminder_when_last_is_assistant():
    big = "x" * 300
    rows = _rows([("user", f"{big} {i}") for i in range(60)])
    rows.append({"role": "assistant", "content": "остання відповідь бота"})
    messages = build_messages(
        _cfg(history_token_limit=100000), rows,
        bot_name="Бот", guild_name="С", channel_name="к",
    )
    assert messages[-1]["role"] == "assistant"
    assert TRAILING_REMINDER not in messages[-1]["content"]


def test_trailing_reminder_threshold_boundary_is_history_only():
    # мало історії (нижче порогу), навіть якщо остання — user
    rows = _rows([("user", "коротко")])
    total = estimate_tokens("коротко") + 8
    assert total < TRAILING_REMINDER_THRESHOLD
    messages = build_messages(
        _cfg(), rows, bot_name="Бот", guild_name="С", channel_name="к",
    )
    assert TRAILING_REMINDER not in messages[-1]["content"]
