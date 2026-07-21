"""Тести LongcatClient: ретраї, класифікація помилок і circuit breaker.

Мережа замокана: підміняємо self._client.chat.completions.create фейком, а
asyncio.sleep — no-op (щоб бекоф не гальмував тести). Час у брейкері беремо з
керованого монотонного годинника, тож cooldown детермінований.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import llm.client as client_mod
from llm.client import LLMError, LongcatClient


def _cfg():
    return SimpleNamespace(
        longcat_api_key="k",
        longcat_base_url="http://x/v1",
        longcat_model="LongCat-2.0",
        max_tokens=100,
        temperature=0.7,
        thinking=None,
        llm_concurrency=2,
    )


def _usage(p=10, c=5):
    return SimpleNamespace(prompt_tokens=p, completion_tokens=c, total_tokens=p + c)


def _response(content="ок"):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=_usage())


class FakeCreate:
    """Скриптований create(): по черзі повертає готову відповідь або кидає
    задану помилку (клас або інстанс)."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def __call__(self, **kwargs):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, type) and issubclass(item, Exception):
            raise _make_error(item)
        return item


def _make_error(cls):
    """Створити інстанс openai-помилки без реального HTTP-респонсу."""
    try:
        return cls.__new__(cls)  # обходимо __init__, що вимагає request/response
    except Exception:  # noqa: BLE001
        return cls("boom")


def _install(client: LongcatClient, script) -> FakeCreate:
    fake = FakeCreate(script)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake))
    )
    return fake


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(client_mod.asyncio, "sleep", instant)


# ---------------- базова поведінка (характеризація) ----------------

async def test_chat_success_returns_result():
    client = LongcatClient(_cfg())
    _install(client, [_response("привіт")])
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.message.content == "привіт"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5


async def test_chat_4xx_raises_immediately():
    from openai import APIStatusError

    client = LongcatClient(_cfg())
    err = APIStatusError.__new__(APIStatusError)
    err.status_code = 401
    err.message = "bad key"
    fake = _install(client, [err])
    with pytest.raises(LLMError):
        await client.chat([{"role": "user", "content": "hi"}])
    # 4xx не ретраїться — рівно один виклик
    assert fake.calls == 1


async def test_chat_retries_then_succeeds():
    from openai import APITimeoutError

    client = LongcatClient(_cfg())
    fake = _install(client, [APITimeoutError, _response("вийшло")])
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.message.content == "вийшло"
    assert fake.calls == 2


async def test_chat_exhausts_all_attempts_raises():
    from openai import APITimeoutError

    client = LongcatClient(_cfg())
    fake = _install(client, [APITimeoutError] * client_mod.MAX_ATTEMPTS)
    with pytest.raises(LLMError):
        await client.chat([{"role": "user", "content": "hi"}])
    assert fake.calls == client_mod.MAX_ATTEMPTS


# ---------------- circuit breaker ----------------

class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _fail_script():
    from openai import APITimeoutError

    return [APITimeoutError] * client_mod.MAX_ATTEMPTS


async def test_breaker_opens_after_threshold(monkeypatch):
    from openai import APITimeoutError

    clock = Clock()
    client = LongcatClient(_cfg())
    monkeypatch.setattr(client_mod.time, "monotonic", clock)

    # доводимо до порога послідовних повних збоїв
    script = [APITimeoutError] * (client_mod.MAX_ATTEMPTS * client_mod.BREAKER_THRESHOLD)
    fake = _install(client, script)
    for _ in range(client_mod.BREAKER_THRESHOLD):
        with pytest.raises(LLMError):
            await client.chat([{"role": "user", "content": "hi"}])

    calls_before = fake.calls
    # брейкер відкритий: наступний виклик падає миттєво, БЕЗ мережі
    with pytest.raises(LLMError):
        await client.chat([{"role": "user", "content": "hi"}])
    assert fake.calls == calls_before  # мережі не було


async def test_breaker_half_open_after_cooldown(monkeypatch):
    clock = Clock()
    client = LongcatClient(_cfg())
    monkeypatch.setattr(client_mod.time, "monotonic", clock)

    # відкриваємо брейкер
    fail = _fail_script() * client_mod.BREAKER_THRESHOLD
    fake = _install(client, fail + [_response("живий")])
    for _ in range(client_mod.BREAKER_THRESHOLD):
        with pytest.raises(LLMError):
            await client.chat([{"role": "user", "content": "hi"}])
    calls_after_open = fake.calls

    # ще на cooldown — миттєвий відмов без мережі
    clock.t += client_mod.BREAKER_COOLDOWN - 1
    with pytest.raises(LLMError):
        await client.chat([{"role": "user", "content": "hi"}])
    assert fake.calls == calls_after_open

    # cooldown минув — half-open пробний запит іде в мережу і успіх закриває брейкер
    clock.t += 2
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.message.content == "живий"
    assert fake.calls == calls_after_open + 1


async def test_breaker_success_resets_counter(monkeypatch):
    from openai import APITimeoutError

    clock = Clock()
    client = LongcatClient(_cfg())
    monkeypatch.setattr(client_mod.time, "monotonic", clock)

    # (THRESHOLD-1) повних збоїв, потім успіх — лічильник має скинутись
    script = [APITimeoutError] * (client_mod.MAX_ATTEMPTS * (client_mod.BREAKER_THRESHOLD - 1))
    script += [_response("ок")]
    _install(client, script)
    for _ in range(client_mod.BREAKER_THRESHOLD - 1):
        with pytest.raises(LLMError):
            await client.chat([{"role": "user", "content": "hi"}])
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.message.content == "ок"

    # після скидання ще один повний збій НЕ відкриває брейкер одразу
    from openai import APITimeoutError as T
    _install(client, [T] * client_mod.MAX_ATTEMPTS + [_response("знову ок")])
    with pytest.raises(LLMError):
        await client.chat([{"role": "user", "content": "hi"}])
    # брейкер ще закритий — наступний виклик іде в мережу
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.message.content == "знову ок"
