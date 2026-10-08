"""Побудова контексту для LLM: системний промпт + історія з тримінгом за токенами.

Вікно LongCat-2.0 — 1M токенів, але кожен запит пересилає всю історію заново,
тому реальний обмежувач — денна квота. CHAT_HISTORY_TOKEN_LIMIT у .env керує
тим, скільки історії їде в кожен запит.
"""

from __future__ import annotations

import datetime


def estimate_tokens(text: str) -> int:
    """Груба оцінка кількості токенів (кирилиця ≈ 2–3 символи/токен).
    Точність тут не критична — це лише бюджет тримінгу."""
    return max(1, len(text) // 3)


DEFAULT_SYSTEM = (
    "Ти — {bot_name}, дружній і корисний асистент у Discord.\n"
    "Правила:\n"
    "- Це чат: відповідай стисло і по суті. Розгорнуто — лише коли явно просять.\n"
    "- Повідомлення користувачів мають префікс «Ім'я:». Свої відповіді пиши БЕЗ префікса.\n"
    "- Відповідай мовою співрозмовника.\n"
    "- Використовуй Discord-markdown: **жирний**, *курсив*, `код`, ```блоки коду```.\n"
    "- Не пінгуй людей через <@id> — називай їх просто на ім'я.\n"
    "- У тебе є інструменти: поточний час, інфо про сервер/користувача, останні повідомлення "
    "каналу, створення нагадувань та опитувань, кидання кубиків. Клич їх, коли потрібні "
    "реальні дані або дія, і відповідай на основі їх результатів.\n"
    "{location}\n"
    "Поточна дата й час: {now}."
)


# Після цього обсягу історії правила з початку промпта «вицвітають» —
# підклеюємо компактне нагадування до останнього повідомлення користувача.
TRAILING_REMINDER_THRESHOLD = 4000


def build_messages(
    cfg,
    history_rows,
    *,
    bot_name: str,
    guild_name: str | None,
    channel_name: str | None,
    system_suffix: str = "",
) -> list[dict]:
    """Формує messages для API: [system] + історія, обрізана під токен-бюджет
    (викидаються найстаріші повідомлення). system_suffix — доважок режиму
    (наприклад, ZZZ-радника), додається після основного промпта."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    if guild_name:
        location = f"Сервер: {guild_name}, канал: #{channel_name or '?'}."
    else:
        location = "Це особисті повідомлення (DM)."

    system = (cfg.system_prompt or "").strip() or DEFAULT_SYSTEM.format(
        bot_name=bot_name, now=now, location=location
    )
    system = f"{system}\n\n{cfg.persona.style_suffix}"
    if system_suffix:
        system = f"{system}\n\n{system_suffix.strip()}"

    budget = cfg.history_token_limit - estimate_tokens(system)
    history_tokens = 0
    selected: list[dict] = []
    for row in reversed(history_rows):  # від найновіших до найстаріших
        cost = estimate_tokens(row["content"]) + 8
        if budget - cost < 0:
            break
        budget -= cost
        history_tokens += cost
        selected.append({"role": row["role"], "content": row["content"]})
    selected.reverse()

    if (
        selected
        and selected[-1]["role"] == "user"
        and history_tokens >= TRAILING_REMINDER_THRESHOLD
    ):
        selected[-1] = {
            **selected[-1],
            "content": selected[-1]["content"] + "\n\n" + cfg.persona.trailing_reminder,
        }

    return [{"role": "system", "content": system}, *selected]
