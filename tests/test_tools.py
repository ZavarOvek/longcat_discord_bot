"""Тести llm.tools: run_agent з фейковим LLM (статистика, tool-цикл, останнє
коло без тулів) і execute_tool (помилки аргументів/JSON/невідомий тул).

Мережеві тули (wiki, web_search) — з мокнутою мережею.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm import tools as tools_mod
from llm.client import ChatResult
from llm.tools import AgentResult, execute_tool, run_agent


# ---------------- фейковий LLM і виклики тулів ----------------

def _tool_call(call_id, name, arguments="{}"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class FakeLLM:
    """Віддає заздалегідь задані відповіді по черзі. Запам'ятовує, чи були
    передані tools у кожному виклику (для перевірки «останнє коло без тулів»)
    і яке значення thinking прокинули (для перевірки пер-режимного вимкнення)."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.tools_seen: list[bool] = []
        self.thinking_seen: list = []
        self.calls = 0

    async def chat(self, messages, tools=None, thinking=None):
        self.tools_seen.append(tools is not None)
        self.thinking_seen.append(thinking)
        self.calls += 1
        message, ptok, ctok = self._scripted.pop(0)
        return ChatResult(message=message, prompt_tokens=ptok, completion_tokens=ctok)


@pytest.fixture
def tctx():
    return SimpleNamespace(bot=None, message=None, db=None)


# ---------------- run_agent: прямий текст ----------------

async def test_run_agent_direct_text(tctx):
    llm = FakeLLM([(_msg(content="проста відповідь"), 100, 20)])
    result = await run_agent(llm, [], tctx, max_iterations=6, schemas=[])
    assert result.text == "проста відповідь"
    assert result.llm_calls == 1
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 20
    assert result.tool_calls == []


async def test_run_agent_empty_content_placeholder(tctx):
    llm = FakeLLM([(_msg(content=""), 10, 0)])
    result = await run_agent(llm, [], tctx, max_iterations=6, schemas=[])
    assert "порожню відповідь" in result.text


# ---------------- run_agent: цикл з інструментом ----------------

async def test_run_agent_tool_then_text(tctx, monkeypatch):
    async def fake_execute(name, arguments, tctx):
        return "результат тула"

    monkeypatch.setattr(tools_mod, "execute_tool", fake_execute)

    llm = FakeLLM([
        (_msg(content=None, tool_calls=[_tool_call("c1", "get_current_time")]), 50, 5),
        (_msg(content="фінальна відповідь"), 60, 10),
    ])
    messages = []
    result = await run_agent(llm, messages, tctx, max_iterations=6, schemas=[{"x": 1}])

    assert result.text == "фінальна відповідь"
    assert result.llm_calls == 2
    assert result.prompt_tokens == 110
    assert result.completion_tokens == 15
    assert len(result.tool_calls) == 1
    # у messages має з'явитися assistant з tool_calls і tool-результат
    roles = [m["role"] for m in messages]
    assert "assistant" in roles and "tool" in roles


async def test_run_agent_last_iteration_no_tools(tctx, monkeypatch):
    async def fake_execute(name, arguments, tctx):
        return "tool result"

    monkeypatch.setattr(tools_mod, "execute_tool", fake_execute)

    # модель уперто кличе тули на кожному кроці
    scripted = [
        (_msg(content=None, tool_calls=[_tool_call(f"c{i}", "get_current_time")]), 10, 1)
        for i in range(5)
    ]
    llm = FakeLLM(scripted)
    result = await run_agent(llm, [], tctx, max_iterations=3, schemas=[{"x": 1}])

    # рівно max_iterations викликів
    assert llm.calls == 3
    # на останньому колі tools НЕ передавались
    assert llm.tools_seen == [True, True, False]
    assert "ліміт кроків" in result.text


async def test_run_agent_accumulates_multiple_tool_calls(tctx, monkeypatch):
    async def fake_execute(name, arguments, tctx):
        return "ok"

    monkeypatch.setattr(tools_mod, "execute_tool", fake_execute)

    llm = FakeLLM([
        (_msg(tool_calls=[
            _tool_call("c1", "get_current_time"),
            _tool_call("c2", "get_server_info"),
        ]), 10, 2),
        (_msg(content="готово"), 5, 1),
    ])
    result = await run_agent(llm, [], tctx, max_iterations=6, schemas=[{"x": 1}])
    assert len(result.tool_calls) == 2


# ---------------- execute_tool: помилки ----------------

async def test_execute_tool_unknown(tctx):
    result = await execute_tool("не_існує", "{}", tctx)
    assert "невідомий інструмент" in result


async def test_execute_tool_bad_json(tctx):
    result = await execute_tool("roll_dice", "{не json}", tctx)
    assert "валідним JSON" in result


async def test_execute_tool_non_object_args(tctx):
    result = await execute_tool("roll_dice", "[1, 2, 3]", tctx)
    assert "JSON-об'єктом" in result


async def test_execute_tool_wrong_arguments(tctx):
    # roll_dice не приймає param «nonexistent»
    result = await execute_tool("roll_dice", '{"nonexistent": 1}', tctx)
    assert "Помилка аргументів" in result


async def test_execute_tool_roll_dice_ok(tctx):
    result = await execute_tool("roll_dice", '{"formula": "1d6"}', tctx)
    assert "1d6" in result


async def test_execute_tool_result_truncated(tctx, monkeypatch):
    async def huge(tctx, **kwargs):
        return "x" * 10000

    monkeypatch.setitem(tools_mod.TOOLS, "huge", huge)
    result = await execute_tool("huge", "{}", tctx)
    assert len(result) == tools_mod.MAX_RESULT_CHARS


async def test_execute_tool_empty_arguments_default(tctx):
    # roll_dice має default formula=1d20
    result = await execute_tool("roll_dice", None, tctx)
    assert "1d20" in result


async def test_execute_tool_exception_becomes_text(tctx, monkeypatch):
    async def boom(tctx, **kwargs):
        raise RuntimeError("вибух")

    monkeypatch.setitem(tools_mod.TOOLS, "boom", boom)
    result = await execute_tool("boom", "{}", tctx)
    assert "Помилка виконання boom" in result
    assert "вибух" in result


# ---------------- веб-тули: деградація без падіння ----------------

async def test_wiki_network_failure_graceful(tctx, monkeypatch):
    class BadClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("мережа лягла")

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", BadClient)
    result = await tools_mod.tool_wiki(tctx, "щось")
    assert "Вікіпедія недоступна" in result


async def test_web_search_failure_graceful(tctx, monkeypatch):
    async def bad_to_thread(fn):
        raise RuntimeError("ddgs впав")

    monkeypatch.setattr(tools_mod.asyncio, "to_thread", bad_to_thread)
    result = await tools_mod.tool_web_search(tctx, "щось")
    assert "Пошук недоступний" in result


async def test_web_search_timeout_graceful(tctx, monkeypatch):
    # ddgs завис -> wait_for кидає TimeoutError -> ввічлива деградація, не виняток
    async def hang_to_thread(fn):
        raise TimeoutError

    monkeypatch.setattr(tools_mod.asyncio, "to_thread", hang_to_thread)
    result = await tools_mod.tool_web_search(tctx, "щось")
    assert "Пошук недоступний" in result


async def test_web_search_wraps_in_wait_for(tctx, monkeypatch):
    # тул має обгортати роботу в asyncio.wait_for із заданим таймаутом
    seen = {}

    async def fake_wait_for(coro, timeout):
        seen["timeout"] = timeout
        coro.close()  # не виконуємо, лише фіксуємо факт обгортання
        return []

    monkeypatch.setattr(tools_mod.asyncio, "wait_for", fake_wait_for)
    result = await tools_mod.tool_web_search(tctx, "щось")
    assert seen["timeout"] == tools_mod.WEB_SEARCH_TIMEOUT
    assert "Нічого не знайдено" in result


# ---------------- run_agent: прокидання thinking ----------------

async def test_run_agent_thinking_passed_through(tctx):
    """thinking із виклику run_agent доходить незмінним до кожного llm.chat."""
    llm = FakeLLM([(_msg(content="ок"), 10, 2)])
    await run_agent(llm, [], tctx, max_iterations=6, schemas=[], thinking=False)
    assert llm.thinking_seen == [False]


async def test_run_agent_thinking_default_none(tctx):
    """Без явного thinking run_agent нічого не нав'язує (None = не чіпати cfg)."""
    llm = FakeLLM([(_msg(content="ок"), 10, 2)])
    await run_agent(llm, [], tctx, max_iterations=6, schemas=[])
    assert llm.thinking_seen == [None]


async def test_run_agent_thinking_same_on_every_iteration(tctx, monkeypatch):
    async def fake_execute(name, arguments, tctx):
        return "ok"

    monkeypatch.setattr(tools_mod, "execute_tool", fake_execute)
    llm = FakeLLM([
        (_msg(content=None, tool_calls=[_tool_call("c1", "get_current_time")]), 10, 1),
        (_msg(content="готово"), 5, 1),
    ])
    await run_agent(llm, [], tctx, max_iterations=6, schemas=[{"x": 1}], thinking=False)
    assert llm.thinking_seen == [False, False]


# ---------------- run_agent: санітайзер розмітки tool-викликів ----------------
# Пінінг інциденту 18.07: LongCat при thinking×tools видає текстові
# <longcat_tool_call> у content замість структурного tool_calls. Санітайзер
# НЕ виконує ці виклики (це зняло б обмеження кроків у момент штопора) —
# він робить один корекційний ретрай, а брудний залишок вирізає регексом.

_DIRTY = "Ось білд <longcat_tool_call>zzz_describe<longcat_arg_key>kind"


async def test_sanitizer_clean_final_untouched(tctx):
    """Чистий фінал без розмітки — жодного зайвого виклику, жодної мітки."""
    llm = FakeLLM([(_msg(content="звичайна відповідь"), 10, 2)])
    result = await run_agent(llm, [], tctx, max_iterations=6, schemas=[])
    assert result.text == "звичайна відповідь"
    assert result.llm_calls == 1
    assert "🧯 маркап-ретрай" not in result.tool_calls


async def test_sanitizer_dirty_then_clean_retry(tctx):
    """Брудний фінал → один корекційний ретрай → чистий текст + мітка."""
    llm = FakeLLM([
        (_msg(content=_DIRTY), 10, 2),
        (_msg(content="Ось нормальний білд без розмітки."), 8, 3),
    ])
    result = await run_agent(llm, [], tctx, max_iterations=6, schemas=[])
    assert result.text == "Ось нормальний білд без розмітки."
    assert "<longcat_tool_call" not in result.text
    assert result.tool_calls == ["🧯 маркап-ретрай"]
    assert result.llm_calls == 2
    # корекційний ретрай іде без інструментів
    assert llm.tools_seen[-1] is False


async def test_sanitizer_double_dirty_strips_and_fallback(tctx):
    """Брудно і в ретраї → вирізаємо блоки; якщо лишилось <30 симв. — фолбек."""
    llm = FakeLLM([
        (_msg(content=_DIRTY), 10, 2),
        (_msg(content="<longcat_tool_call>zzz_search<longcat_arg_key>query"), 8, 3),
    ])
    result = await run_agent(llm, [], tctx, max_iterations=6, schemas=[])
    assert "<longcat_tool_call" not in result.text
    assert "переформулюй" in result.text or "/reset" in result.text
    assert result.tool_calls == ["🧯 маркап-ретрай"]


async def test_sanitizer_double_dirty_strips_keeps_remainder(tctx):
    """Брудно і в ретраї, але лишається >30 симв. корисного тексту — беремо його."""
    tail = "Коротко: качай сигнатурку, диски 4+2, це головне для цього агента."
    llm = FakeLLM([
        (_msg(content=_DIRTY), 10, 2),
        (_msg(content=f"{tail} <longcat_tool_call>zzz_search<longcat_arg_key>q"), 8, 3),
    ])
    result = await run_agent(llm, [], tctx, max_iterations=6, schemas=[])
    assert "<longcat_tool_call" not in result.text
    assert tail in result.text
    assert result.tool_calls == ["🧯 маркап-ретрай"]


async def test_sanitizer_markup_not_in_final_ignored(tctx, monkeypatch):
    """Розмітка в проміжному кроці (де є структурні tool_calls) не тригерить
    санітайзер — чіпаємо лише фінальний текстовий крок."""
    async def fake_execute(name, arguments, tctx):
        return "ok"

    monkeypatch.setattr(tools_mod, "execute_tool", fake_execute)
    llm = FakeLLM([
        # проміжний крок: є структурні tool_calls, а в content — сміття
        (_msg(content=_DIRTY, tool_calls=[_tool_call("c1", "get_current_time")]), 10, 1),
        (_msg(content="Чистий фінал."), 5, 1),
    ])
    result = await run_agent(llm, [], tctx, max_iterations=6, schemas=[{"x": 1}])
    assert result.text == "Чистий фінал."
    assert "🧯 маркап-ретрай" not in result.tool_calls
    assert result.llm_calls == 2


# ---------------- AgentResult дефолти ----------------

def test_agent_result_defaults():
    r = AgentResult()
    assert r.text == ""
    assert r.tool_calls == []
    assert r.prompt_tokens == 0
    assert r.llm_calls == 0
