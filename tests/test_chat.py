"""Тести cogs.chat:
- build_footer / build_embeds (чисте оформлення, без Discord-мережі);
- _clear_history лишає RESET_MARKER першим у пам'яті;
- мовний вартовий у _run: український текст при lang_guard="ru" ретраїться,
  вдалий ретрай замінює відповідь і додає ярлик; невдалий — лишає як є.

run_agent мокається на рівні модуля cogs.chat, тому реальний LLM не потрібен.
Discord-обʼєкти (bot, message, channel) — легкі SimpleNamespace-фейки.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import cogs.chat as chat_mod
from cogs.chat import ChatCog, build_embeds, build_footer, build_quota_text
from llm.tools import AgentResult


# ---------------- build_footer ----------------

def test_footer_tokens_only():
    result = AgentResult(text="x", prompt_tokens=1500, completion_tokens=300)
    footer = build_footer(result)
    assert "🎫" in footer
    assert "1.5k" in footer and "0.3k" in footer
    assert "🔧" not in footer  # тулів не було
    assert "⛓" not in footer   # один виклик


def test_footer_lists_tools():
    result = AgentResult(
        text="x", tool_calls=["a", "b", "c"], prompt_tokens=0, completion_tokens=0, llm_calls=3
    )
    footer = build_footer(result)
    assert "🔧 a · b · c" in footer
    assert "⛓ 3 виклики LLM" in footer


def test_footer_truncates_tool_list():
    result = AgentResult(text="x", tool_calls=[f"t{i}" for i in range(7)])
    footer = build_footer(result)
    # показує перші 4 і лічильник решти
    assert "t0 · t1 · t2 · t3 +3" in footer
    assert "t4" not in footer


# ---------------- build_quota_text ----------------

def test_quota_text_empty():
    empty = {"replies": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "top_mode": None}
    text = build_quota_text(empty, empty)
    # без запитів — зрозуміле повідомлення, без падіння на None-режимі
    assert "0" in text
    assert "|" not in text  # не |-таблиця (Discord її не рендерить)


def test_quota_text_reports_numbers():
    today = {"replies": 3, "prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500, "top_mode": "zzz"}
    week = {"replies": 20, "prompt_tokens": 9000, "completion_tokens": 2000, "total_tokens": 11000, "top_mode": "normal"}
    text = build_quota_text(today, week)
    assert "3" in text and "1500" in text
    assert "20" in text and "11000" in text
    assert "|" not in text


# ---------------- build_embeds ----------------

def test_embeds_one_per_chunk_footer_on_last():
    embeds = build_embeds(["перший", "другий"], zzz=False, footer="хвіст")
    assert len(embeds) == 2
    assert embeds[0].description == "перший"
    assert embeds[1].description == "другий"
    # футер лише на останньому
    assert embeds[0].footer.text is None
    assert embeds[1].footer.text == "хвіст"
    assert embeds[0].color == chat_mod.COLOR_NORMAL


def test_embeds_zzz_badge_and_color():
    embeds = build_embeds(["текст"], zzz=True, footer=None)
    assert embeds[0].color == chat_mod.COLOR_ZZZ
    assert embeds[0].author.name == "⚡ ZZZ-радник"


def test_embeds_no_footer_when_none():
    embeds = build_embeds(["a", "b"], zzz=False, footer=None)
    assert all(e.footer.text is None for e in embeds)


def test_embeds_zzz_badge_only_on_first():
    embeds = build_embeds(["a", "b", "c"], zzz=True, footer=None)
    assert embeds[0].author.name == "⚡ ZZZ-радник"
    assert embeds[1].author.name is None
    assert embeds[2].author.name is None


# ---------------- _build_payloads ----------------

def _bare_cog():
    return ChatCog(SimpleNamespace(db=None, config=None, llm=None, user=None, zzz_db=None))


def test_payloads_embed_mode_wraps_embeds():
    cog = _bare_cog()
    payloads = cog._build_payloads("короткий текст", zzz_mode=True, footer="хвіст", embed=True)
    assert len(payloads) == 1
    assert "embed" in payloads[0]
    assert payloads[0]["embed"].description == "короткий текст"
    assert payloads[0]["embed"].footer.text == "хвіст"


def test_payloads_plain_mode_appends_footer_inline():
    cog = _bare_cog()
    payloads = cog._build_payloads("привіт", zzz_mode=False, footer="🎫 1.0k", embed=False)
    assert len(payloads) == 1
    assert payloads[0]["content"].endswith("-# 🎫 1.0k")
    assert payloads[0]["content"].startswith("привіт")


def test_payloads_plain_footer_separate_when_too_long():
    cog = _bare_cog()
    # останній чанк майже повний + довгий футер не влазять разом -> футер окремо
    text = "я" * 1899
    long_footer = "ф" * 200
    payloads = cog._build_payloads(text, zzz_mode=False, footer=long_footer, embed=False)
    assert payloads[-1]["content"] == f"-# {long_footer}"
    assert len(payloads) >= 2


def test_payloads_plain_no_footer():
    cog = _bare_cog()
    payloads = cog._build_payloads("текст", zzz_mode=False, footer=None, embed=False)
    assert payloads == [{"content": "текст"}]


# ---------------- фейкові Discord/БД/LLM ----------------

class FakeDB:
    """Мінімальна БД для чат-кога: історія в пам'яті + режим каналу."""

    def __init__(self, history=None, mode=None, usage_raises=False):
        self.messages = list(history or [])
        self.mode = mode
        self.cleared = 0
        self.usage_calls = []
        self.usage_raises = usage_raises

    async def get_chat_history(self, channel_id):
        return list(self.messages)

    async def add_chat_message(self, channel_id, guild_id, role, content):
        self.messages.append({"role": role, "content": content})

    async def clear_chat_history(self, channel_id):
        n = len(self.messages)
        self.messages.clear()
        self.cleared = n
        return n

    async def get_channel_mode(self, channel_id):
        return self.mode

    async def log_usage(self, channel_id, guild_id, prompt_tokens, completion_tokens, mode):
        if self.usage_raises:
            raise RuntimeError("БД лягла на записі usage")
        self.usage_calls.append(
            dict(channel_id=channel_id, guild_id=guild_id, prompt_tokens=prompt_tokens,
                 completion_tokens=completion_tokens, mode=mode)
        )


def _config(**over):
    base = dict(
        web_tools=False,
        lang_guard="ru",
        history_token_limit=24000,
        max_tool_iterations=6,
        system_prompt="",
        max_tokens=2048,
        user_cooldown_seconds=0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _bot(db, cfg):
    user = SimpleNamespace(id=999, display_name="Котстер")
    return SimpleNamespace(db=db, config=cfg, llm=object(), user=user, zzz_db=None)


def _message(bot, content="Привіт", channel_id=42):
    channel = SimpleNamespace(id=channel_id, name="general")
    guild = SimpleNamespace(id=7, name="Хата")
    return SimpleNamespace(
        content=content,
        channel=channel,
        guild=guild,
        author=SimpleNamespace(id=1, display_name="Юзер"),
    )


# ---------------- _clear_history ----------------

@pytest.mark.asyncio
async def test_clear_history_leaves_reset_marker():
    db = FakeDB(history=[{"role": "user", "content": "старе"}, {"role": "assistant", "content": "теж старе"}])
    cog = ChatCog(_bot(db, _config()))
    deleted = await cog._clear_history(channel_id=42, guild_id=7)
    assert deleted == 2
    # після чистки лишається рівно одна позначка — і саме RESET_MARKER
    assert db.messages == [{"role": "user", "content": ChatCog.RESET_MARKER}]


@pytest.mark.asyncio
async def test_clear_history_empty_channel():
    db = FakeDB(history=[])
    cog = ChatCog(_bot(db, _config()))
    deleted = await cog._clear_history(channel_id=42, guild_id=None)
    assert deleted == 0
    assert db.messages == [{"role": "user", "content": ChatCog.RESET_MARKER}]


# ---------------- мовний вартовий у _run ----------------

@pytest.mark.asyncio
async def test_lang_guard_retries_ukrainian(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="ru")))

    # 1-й прогін — українською (вартовий має спрацювати), ретрай — російською.
    ua = "Привіт! Її їжа їде їжею, ґрунт і їжак — ось моя відповідь тобі їй їм"
    scripted = [
        AgentResult(text=ua, llm_calls=1),
        AgentResult(text="Привет, это ответ по-русски", prompt_tokens=10, completion_tokens=5, llm_calls=1),
    ]

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return scripted.pop(0)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, zzz_mode = await cog._run(_message(cog.bot))
    assert zzz_mode is False
    assert result.text == "Привет, это ответ по-русски"
    assert "🌐 мовний ретрай" in result.tool_calls
    # статистика ретраю додана до основного результату
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    # відповідь збережена в історію
    assert db.messages[-1] == {"role": "assistant", "content": "Привет, это ответ по-русски"}


@pytest.mark.asyncio
async def test_lang_guard_keeps_russian(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="ru")))

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return AgentResult(text="Уже по-русски, всё хорошо", llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, _ = await cog._run(_message(cog.bot))
    assert result.text == "Уже по-русски, всё хорошо"
    assert "🌐 мовний ретрай" not in result.tool_calls


@pytest.mark.asyncio
async def test_lang_guard_disabled(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="")))

    calls = []

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        calls.append(iters)
        return AgentResult(text="Її їжа їде їжею, ґрунт і їжак — українською їй їм", llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, _ = await cog._run(_message(cog.bot))
    # вартовий вимкнено — ретраю немає, лишається українська
    assert calls == [6]  # рівно один прогін (max_tool_iterations)
    assert "🌐 мовний ретрай" not in result.tool_calls


@pytest.mark.asyncio
async def test_lang_guard_failed_retry_keeps_original(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="ru")))

    original = "Українською їжею ґрунт відповідь їжа їжа"
    scripted = [
        AgentResult(text=original, llm_calls=1),
        # ретрай теж українською -> кандидат відхиляється, лишається original
        AgentResult(text="Її їжа їде їжею, ґрунт і їжак — знову українською їй їм", llm_calls=1),
    ]

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return scripted.pop(0)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, _ = await cog._run(_message(cog.bot))
    assert result.text == original
    assert "🌐 мовний ретрай" not in result.tool_calls
    # виправлення не вдалося -> в історію лягає оригінал
    assert db.messages[-1] == {"role": "assistant", "content": original}


# ---------------- характеризація _run (фіксація до рефакторингу) ----------------
# Ці тести пришпилюють поточну поведінку _run: порядок fix_tables → вартовий →
# мітки → запис в історію. Після розбиття _run вони мають лишитись незмінними.


class FakeZZZ:
    """Мінімальний zzz_db: версія в meta + детермінований auto_context."""

    def __init__(self, block="", labels=None):
        self.meta = {"game_version": "2.0"}
        self._block = block
        self._labels = list(labels or [])

    def auto_context(self, text):
        return self._block, list(self._labels)


@pytest.mark.asyncio
async def test_run_no_guard_stores_and_returns(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="")))

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return AgentResult(text="Обычный ответ", llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, zzz_mode = await cog._run(_message(cog.bot))
    assert zzz_mode is False
    assert result.text == "Обычный ответ"
    assert db.messages[-1] == {"role": "assistant", "content": "Обычный ответ"}


@pytest.mark.asyncio
async def test_run_applies_fix_tables_before_guard(monkeypatch):
    # fix_tables перетворює |-таблицю на рядки; вартовий дивиться вже на виправлений текст
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="ru")))

    raw = "| A | B |\n| --- | --- |\n| 1 | 2 |"

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return AgentResult(text=raw, llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, _ = await cog._run(_message(cog.bot))
    # таблиця не лишилась сирою (| не рендериться в Discord)
    assert "| --- |" not in result.text
    assert result.text == db.messages[-1]["content"]


@pytest.mark.asyncio
async def test_run_retry_called_at_most_once(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="ru")))

    ua = "Її їжа їде їжею, ґрунт і їжак — українською їй їм ще"
    calls = []
    # обидва прогони українською: якби ретраїв було >1, вартовий зациклився б
    scripted = [AgentResult(text=ua, llm_calls=1), AgentResult(text=ua, llm_calls=1)]

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        calls.append(iters)
        return scripted.pop(0)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, _ = await cog._run(_message(cog.bot))
    # рівно два виклики: основний (iters=max) + один ретрай (iters=1)
    assert calls == [6, 1]
    assert "🌐 мовний ретрай" not in result.tool_calls  # ретрай теж укр -> оригінал


@pytest.mark.asyncio
async def test_run_autocontext_labels_prefixed(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: розкажи про Miyabi"}], mode="zzz")
    bot = _bot(db, _config(lang_guard=""))
    bot.zzz_db = FakeZZZ(block="[дані про Miyabi]", labels=["Miyabi", "Yanagi"])
    cog = ChatCog(bot)

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return AgentResult(text="Ответ по ZZZ", tool_calls=["🔧 zzz_search"], llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, zzz_mode = await cog._run(_message(cog.bot))
    assert zzz_mode is True
    # мітки 📦 авто-контексту стоять ПОПЕРЕДУ решти tool_calls, у порядку labels
    assert result.tool_calls[:2] == ["📦 Miyabi", "📦 Yanagi"]
    assert result.tool_calls[2] == "🔧 zzz_search"


@pytest.mark.asyncio
async def test_run_autocontext_labels_before_guard_label(monkeypatch):
    # порядок у tool_calls: [📦 авто-контекст ..., 🌐 мовний ретрай, ...]
    db = FakeDB(history=[{"role": "user", "content": "Юзер: про Miyabi"}], mode="zzz")
    bot = _bot(db, _config(lang_guard="ru"))
    bot.zzz_db = FakeZZZ(block="[дані]", labels=["Miyabi"])
    cog = ChatCog(bot)

    ua = "Її їжа їде їжею, ґрунт і їжак — українською їй їм"
    scripted = [
        AgentResult(text=ua, llm_calls=1),
        AgentResult(text="Ответ по-русски", llm_calls=1),
    ]

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return scripted.pop(0)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, _ = await cog._run(_message(cog.bot))
    assert result.text == "Ответ по-русски"
    # мітка авто-контексту попереду, мітка вартового — після неї
    assert result.tool_calls == ["📦 Miyabi", "🌐 мовний ретрай"]


# ---------------- пер-режимне вимкнення thinking (шар 1 фіксу thinking×tools) ----------------
# Корінь інциденту 18.07: у zzz-каналах мислення примусово вимкнене (thinking=False),
# бо thinking×function-calling у LongCat дає текстові <longcat_tool_call> у content.


@pytest.mark.asyncio
async def test_run_thinking_disabled_in_zzz(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: про Miyabi"}], mode="zzz")
    bot = _bot(db, _config(lang_guard=""))
    bot.zzz_db = FakeZZZ(block="", labels=[])
    cog = ChatCog(bot)

    seen = []

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        seen.append(thinking)
        return AgentResult(text="Ответ по ZZZ", llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, zzz_mode = await cog._run(_message(cog.bot))
    assert zzz_mode is True
    # у zzz-режимі мислення примусово вимкнене
    assert seen == [False]


@pytest.mark.asyncio
async def test_run_thinking_none_in_normal(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="")))

    seen = []

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        seen.append(thinking)
        return AgentResult(text="Обычный ответ", llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    await cog._run(_message(cog.bot))
    # звичайний режим нічого не нав'язує — рішення за cfg.thinking усередині клієнта
    assert seen == [None]


@pytest.mark.asyncio
async def test_run_lang_guard_retry_inherits_thinking(monkeypatch):
    # ретрай мовного вартового в zzz успадковує thinking=False основного прогону
    db = FakeDB(history=[{"role": "user", "content": "Юзер: про Miyabi"}], mode="zzz")
    bot = _bot(db, _config(lang_guard="ru"))
    bot.zzz_db = FakeZZZ(block="", labels=[])
    cog = ChatCog(bot)

    ua = "Її їжа їде їжею, ґрунт і їжак — українською їй їм"
    scripted = [
        AgentResult(text=ua, llm_calls=1),
        AgentResult(text="Ответ по-русски", llm_calls=1),
    ]
    seen = []

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        seen.append(thinking)
        return scripted.pop(0)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, _ = await cog._run(_message(cog.bot))
    assert result.text == "Ответ по-русски"
    # обидва виклики (основний + ретрай вартового) з thinking=False
    assert seen == [False, False]


# ---------------- облік витрат токенів (usage_log) у _run ----------------


@pytest.mark.asyncio
async def test_run_logs_usage_normal_mode(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="")))

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return AgentResult(text="Ответ", prompt_tokens=120, completion_tokens=30, llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    await cog._run(_message(cog.bot))
    assert len(db.usage_calls) == 1
    call = db.usage_calls[0]
    assert call["channel_id"] == 42
    assert call["guild_id"] == 7
    assert call["prompt_tokens"] == 120
    assert call["completion_tokens"] == 30
    assert call["mode"] == "normal"


@pytest.mark.asyncio
async def test_run_logs_usage_zzz_mode(monkeypatch):
    db = FakeDB(history=[{"role": "user", "content": "Юзер: про Miyabi"}], mode="zzz")
    bot = _bot(db, _config(lang_guard=""))
    bot.zzz_db = FakeZZZ(block="", labels=[])
    cog = ChatCog(bot)

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return AgentResult(text="Ответ по ZZZ", prompt_tokens=200, completion_tokens=40, llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    await cog._run(_message(cog.bot))
    assert db.usage_calls[0]["mode"] == "zzz"


@pytest.mark.asyncio
async def test_run_logs_usage_includes_guard_retry_tokens(monkeypatch):
    # запис має відображати сумарні токени (основний прогін + ретрай вартового)
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}])
    cog = ChatCog(_bot(db, _config(lang_guard="ru")))

    ua = "Привіт! Її їжа їде їжею, ґрунт і їжак — ось моя відповідь тобі їй їм"
    scripted = [
        AgentResult(text=ua, prompt_tokens=100, completion_tokens=20, llm_calls=1),
        AgentResult(text="Привет, по-русски", prompt_tokens=10, completion_tokens=5, llm_calls=1),
    ]

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return scripted.pop(0)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    await cog._run(_message(cog.bot))
    call = db.usage_calls[0]
    assert call["prompt_tokens"] == 110
    assert call["completion_tokens"] == 25


@pytest.mark.asyncio
async def test_run_usage_logging_failure_does_not_break_reply(monkeypatch):
    # якщо log_usage кидає — відповідь усе одно повертається без винятку
    db = FakeDB(history=[{"role": "user", "content": "Юзер: питання"}], usage_raises=True)
    cog = ChatCog(_bot(db, _config(lang_guard="")))

    async def fake_run_agent(llm, messages, tctx, iters, *, schemas, thinking=None):
        return AgentResult(text="Ответ", prompt_tokens=1, completion_tokens=1, llm_calls=1)

    monkeypatch.setattr(chat_mod, "run_agent", fake_run_agent)

    result, _ = await cog._run(_message(cog.bot))
    assert result.text == "Ответ"


# ---------------- пер-юзерний антифлуд-кулдаун ----------------


def test_user_cooldown_disabled_always_allows():
    cog = ChatCog(_bot(FakeDB(), _config(user_cooldown_seconds=0)))
    assert cog._check_user_cooldown(1, now=100.0) is True
    # навіть одразу поспіль — вимкнено = завжди дозволено
    assert cog._check_user_cooldown(1, now=100.0) is True


def test_user_cooldown_blocks_within_window():
    cog = ChatCog(_bot(FakeDB(), _config(user_cooldown_seconds=5)))
    assert cog._check_user_cooldown(1, now=100.0) is True   # перший — дозволено
    assert cog._check_user_cooldown(1, now=102.0) is False  # за 2 с — рано
    assert cog._check_user_cooldown(1, now=106.0) is True   # за 6 с — можна


def test_user_cooldown_is_per_user():
    cog = ChatCog(_bot(FakeDB(), _config(user_cooldown_seconds=5)))
    assert cog._check_user_cooldown(1, now=100.0) is True
    # інший юзер не чіпається кулдауном першого
    assert cog._check_user_cooldown(2, now=100.0) is True


def test_user_cooldown_sweeps_stale_keys():
    cog = ChatCog(_bot(FakeDB(), _config(user_cooldown_seconds=5)))
    # набиваємо словник до порога чистки протухлими ключами
    for uid in range(chat_mod.USER_COOLDOWN_SWEEP_EVERY):
        cog._check_user_cooldown(uid, now=0.0)
    # свіжий дотик далеко в майбутньому тригерить sweep протухлих
    cog._check_user_cooldown(999999, now=10_000.0)
    # старі ключі (їх кулдаун давно сплив) прибрані
    assert len(cog._user_cooldowns) < chat_mod.USER_COOLDOWN_SWEEP_EVERY
