"""Асинхронний клієнт LongCat (OpenAI-сумісний ендпоінт /openai/v1).

Особливості:
- глобальний семафор: не більше N одночасних запитів до API
- ретраї з експоненційним бекофом на 429 / таймаути / 5xx
- опційний параметр thinking (LongCat-специфіка), передається через extra_body
- логування usage-токенів у лог — зручно стежити за квотою
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, NamedTuple

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 4

# Circuit breaker: після BREAKER_THRESHOLD поспіль повних збоїв (усі спроби
# вичерпано — мережа/квота лежать) розмикаємо коло на BREAKER_COOLDOWN секунд.
# У розімкненому стані chat() падає миттєво, без спроб мережі; перший запит
# після cooldown — пробний (half-open): успіх замикає коло, збій знову розмикає.
# 4xx (невірний ключ/запит) НЕ рахуються — це не збій сервісу, а помилка виклику.
BREAKER_THRESHOLD = 5
BREAKER_COOLDOWN = 60.0


class ChatResult(NamedTuple):
    """Відповідь моделі + витрати токенів цього виклику."""

    message: Any
    prompt_tokens: int
    completion_tokens: int


class LLMError(RuntimeError):
    """Помилка звернення до LongCat, яку варто показати користувачу."""


class LongcatClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._client = AsyncOpenAI(
            api_key=cfg.longcat_api_key,
            base_url=cfg.longcat_base_url,
            timeout=180.0,
            max_retries=0,  # ретраї робимо самі, з контролем бекофу
        )
        self._semaphore = asyncio.Semaphore(cfg.llm_concurrency)
        # Стан circuit breaker: лічильник послідовних повних збоїв і час, до
        # якого коло лишається розімкненим (0 = замкнене).
        self._breaker_failures = 0
        self._breaker_open_until = 0.0

    def _breaker_ok(self) -> bool:
        """True, якщо запит дозволено (коло замкнене або cooldown минув)."""
        return time.monotonic() >= self._breaker_open_until

    def _breaker_record_success(self) -> None:
        self._breaker_failures = 0
        self._breaker_open_until = 0.0

    def _breaker_record_failure(self) -> None:
        self._breaker_failures += 1
        if self._breaker_failures >= BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + BREAKER_COOLDOWN
            log.warning(
                "Circuit breaker розімкнено: %d повних збоїв поспіль, пауза %.0f с",
                self._breaker_failures, BREAKER_COOLDOWN,
            )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        thinking: bool | None = None,
    ) -> ChatResult:
        """Один виклик chat.completions. Повертає ChatResult:
        message (content / tool_calls) + токени prompt/completion.

        thinking перекриває cfg.thinking для цього виклику (пер-режимний
        контроль): режим ZZZ вимикає мислення, бо thinking×function-calling у
        LongCat дає текстові <longcat_tool_call> у content замість структурних
        tool_calls. None означає «взяти cfg.thinking»."""
        effective_thinking = self.cfg.thinking if thinking is None else thinking
        kwargs: dict = {
            "model": self.cfg.longcat_model,
            "messages": messages,
            "max_tokens": self.cfg.max_tokens,
            "temperature": self.cfg.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if effective_thinking is not None:
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled" if effective_thinking else "disabled"}
            }

        # Circuit breaker: якщо коло розімкнене й cooldown ще не минув — падаємо
        # миттєво, не смикаючи мережу (сервіс однаково лежить).
        if not self._breaker_ok():
            raise LLMError(
                "LongCat тимчасово недоступний (забагато збоїв поспіль) — зачекай близько хвилини."
            )

        delay = 2.0
        async with self._semaphore:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    response = await self._client.chat.completions.create(**kwargs)
                    usage = response.usage
                    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                    if usage:
                        log.info(
                            "LLM usage: prompt=%s, completion=%s, total=%s",
                            prompt_tokens,
                            completion_tokens,
                            getattr(usage, "total_tokens", prompt_tokens + completion_tokens),
                        )
                    self._breaker_record_success()
                    return ChatResult(response.choices[0].message, prompt_tokens, completion_tokens)
                except RateLimitError:
                    log.warning("LongCat 429 (rate limit), спроба %d/%d — чекаю %.0f с",
                                attempt, MAX_ATTEMPTS, delay)
                except (APITimeoutError, APIConnectionError, InternalServerError) as exc:
                    log.warning("LongCat %s, спроба %d/%d — чекаю %.0f с",
                                type(exc).__name__, attempt, MAX_ATTEMPTS, delay)
                except APIStatusError as exc:
                    # 4xx, які ретраїти немає сенсу (невірний ключ, поганий запит тощо)
                    body = getattr(exc, "message", None) or str(exc)
                    raise LLMError(f"LongCat API {exc.status_code}: {body}") from exc

                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(delay + random.uniform(0, 1))
                    delay = min(delay * 2, 45)

        self._breaker_record_failure()
        raise LLMError("LongCat недоступний після кількох спроб (rate limit або мережа). Спробуй пізніше.")
