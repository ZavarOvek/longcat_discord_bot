"""Тести persona.load_persona і протягування персони в код.

Файл персони необов'язковий: немає — працюють вбудовані тексти. Є — кожен
ключ перекриває лише своє поле, а будь-яка помилка у файлі валить старт із
назвою ключа (мовчазний дефолт на персоні — це тижні незрозумілої поведінки).

Місця використання перевіряються на маркерних рядках: беремо персону з явно
впізнаваними текстами і дивимось, що саме вони дійшли до адресата.
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import cogs.chat as chat_mod
import llm.tools as tools_mod
from cogs.chat import ChatCog
from llm.memory import build_messages
from persona import DEFAULT_PERSONA, PERSONA_KEYS, Persona, load_persona
from zzz.db import ZZZDatabase

# ---------------- load_persona ----------------


def test_missing_file_gives_defaults(tmp_path):
    assert load_persona(str(tmp_path / "немає.toml")) == DEFAULT_PERSONA


def test_single_key_overrides_only_itself(tmp_path):
    file = tmp_path / "persona.toml"
    file.write_text('reset_marker = "своя позначка"\n', encoding="utf-8")

    persona = load_persona(str(file))
    assert persona.reset_marker == "своя позначка"
    # решта полів лишилась вбудованою
    others = [key for key in PERSONA_KEYS if key != "reset_marker"]
    assert all(getattr(persona, key) == getattr(DEFAULT_PERSONA, key) for key in others)


def test_unknown_key_exits_and_names_it(tmp_path, capsys):
    file = tmp_path / "persona.toml"
    file.write_text('reset_markerr = "одруківка"\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        load_persona(str(file))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "reset_markerr" in err
    # і перелік допустимих ключів, щоб одруківку було видно одразу
    assert "reset_marker" in err


def test_non_string_value_exits_and_names_key(tmp_path, capsys):
    file = tmp_path / "persona.toml"
    file.write_text("style_suffix = 42\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        load_persona(str(file))
    assert exc.value.code == 1
    assert "style_suffix" in capsys.readouterr().err


def test_foreign_placeholder_in_zzz_prompt_exits(tmp_path, capsys):
    file = tmp_path / "persona.toml"
    file.write_text('zzz_mode_prompt = "версія {version}, а ще {oops}"\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        load_persona(str(file))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "zzz_mode_prompt" in err
    assert "version" in err


# ---------------- протягування персони в код ----------------


def _persona(**over) -> Persona:
    return replace(DEFAULT_PERSONA, **over)


def test_build_messages_takes_suffix_and_reminder_from_persona():
    persona = _persona(style_suffix="МАРКЕР-ОФОРМЛЕННЯ", trailing_reminder="МАРКЕР-НАГАДУВАННЯ")
    cfg = SimpleNamespace(system_prompt="X", history_token_limit=100000, persona=persona)
    # історії вистачає, щоб перевищити поріг хвостового нагадування
    rows = [{"role": "user", "content": "x" * 400} for _ in range(40)]

    messages = build_messages(cfg, rows, bot_name="Бот", guild_name="С", channel_name="к")
    assert "МАРКЕР-ОФОРМЛЕННЯ" in messages[0]["content"]
    assert "МАРКЕР-НАГАДУВАННЯ" in messages[-1]["content"]
    # вбудовані тексти не протікають повз персону
    assert DEFAULT_PERSONA.style_suffix not in messages[0]["content"]


@pytest.mark.asyncio
async def test_clear_history_writes_persona_reset_marker():
    written: list[tuple] = []

    class FakeDB:
        async def clear_chat_history(self, channel_id):
            return 3

        async def add_chat_message(self, channel_id, guild_id, role, content):
            written.append((role, content))

    config = SimpleNamespace(persona=_persona(reset_marker="МАРКЕР-ЧИСТКИ"))
    cog = ChatCog(SimpleNamespace(db=FakeDB(), config=config))

    await cog._clear_history(channel_id=42, guild_id=7)
    assert written == [("user", "МАРКЕР-ЧИСТКИ")]


@pytest.mark.asyncio
async def test_sanitize_markup_sends_note_from_parameter():
    sent: list[dict] = []

    class FakeLLM:
        async def chat(self, messages, tools=None, thinking=None):
            sent.extend(messages)
            return SimpleNamespace(
                message=SimpleNamespace(content="чистий текст", tool_calls=None),
                prompt_tokens=1,
                completion_tokens=1,
            )

    stats = tools_mod.AgentResult(text="брудно <longcat_tool_call>zzz_search")
    await tools_mod._sanitize_markup(stats, FakeLLM(), [], None, markup_retry_note="МАРКЕР-РЕТРАЮ")
    assert sent[-1] == {"role": "user", "content": "МАРКЕР-РЕТРАЮ"}
    assert stats.text == "чистий текст"


def _zzz_db(tmp_path) -> ZZZDatabase:
    """Мінімальна, але справжня база: auto_context іде через реальний індекс."""
    root = tmp_path / "zzz"
    root.mkdir()
    payload = {
        "agents.json": {"1091": {"name": "Miyabi", "rarity": "S", "element": "Ice"}},
        "wengines.json": {},
        "discs.json": {},
        "bangboo.json": {},
        "meta.json": {"game_version": "3.0"},
    }
    for name, data in payload.items():
        (root / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return ZZZDatabase(root).load()


def test_auto_context_uses_given_header(tmp_path):
    db = _zzz_db(tmp_path)

    block, labels = db.auto_context("розкажи про Miyabi", header="МАРКЕР-ШАПКИ")
    assert labels == ["Miyabi"]
    assert block.startswith("МАРКЕР-ШАПКИ")
    assert DEFAULT_PERSONA.zzz_reference_header not in block


@pytest.mark.asyncio
async def test_resolve_mode_builds_zzz_prompt_from_persona(tmp_path):
    """У zzz-режимі і промпт режиму, і шапка авто-контексту — з персони."""

    class FakeDB:
        async def get_channel_mode(self, channel_id):
            return "zzz"

    persona = _persona(
        zzz_mode_prompt="МАРКЕР-РЕЖИМУ (версія {version})",
        zzz_reference_header="МАРКЕР-ШАПКИ",
    )
    config = SimpleNamespace(persona=persona, web_tools=False)
    bot = SimpleNamespace(db=FakeDB(), config=config, zzz_db=_zzz_db(tmp_path))
    cog = ChatCog(bot)
    message = SimpleNamespace(channel=SimpleNamespace(id=42, name="general"))
    rows = [{"role": "user", "content": "Юзер: розкажи про Miyabi"}]

    system_suffix, _schemas, zzz_mode, labels, thinking = await cog._resolve_mode(message, rows)
    assert zzz_mode is True
    assert thinking is chat_mod.ZZZ_THINKING_OVERRIDE
    assert system_suffix.startswith("МАРКЕР-РЕЖИМУ (версія 3.0)")
    # шапка авто-контексту теж із персони, а не вбудована
    assert "МАРКЕР-ШАПКИ" in system_suffix
    assert labels == ["Miyabi"]
