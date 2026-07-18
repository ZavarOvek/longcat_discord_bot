"""Тести utils: split_message, fix_tables, parse_duration, looks_ukrainian, roll_dice."""
from __future__ import annotations

import random

import pytest

from utils import (
    SAFE_LIMIT,
    fix_tables,
    looks_ukrainian,
    parse_duration,
    roll_dice,
    split_message,
)


# ---------------- split_message ----------------

def test_split_short_text_single_chunk():
    assert split_message("привіт") == ["привіт"]


def test_split_empty_returns_placeholder():
    assert split_message("") == ["…"]
    assert split_message("   ") == ["…"]


def test_split_respects_limit():
    text = "а" * 5000
    chunks = split_message(text, limit=1000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)


def test_split_reassembles_content():
    text = "\n\n".join(f"Абзац номер {i} з якимось текстом." for i in range(200))
    chunks = split_message(text, limit=500)
    joined = "".join(chunks)
    # усі непробільні символи мають зберегтися
    assert joined.replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_split_prefers_paragraph_boundary():
    text = "Перший абзац.\n\n" + "х" * 100 + "\n\n" + "Другий абзац."
    chunks = split_message(text, limit=60)
    assert len(chunks) >= 2


def test_split_closes_open_code_fence():
    # код-блок, розрізаний навпіл, має бути закритий і перевідкритий
    code = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(80)) + "\n```"
    chunks = split_message(code, limit=200)
    assert len(chunks) > 1
    # кожен шматок з відкритим блоком має бути збалансований
    for chunk in chunks:
        assert chunk.count("```") % 2 == 0


def test_split_reopens_fence_with_language():
    code = "```py\n" + "x" * 3000 + "\n```"
    chunks = split_message(code, limit=500)
    # продовження блоку має нести мову ```py
    assert chunks[1].startswith("```py")


# ---------------- parse_duration ----------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10m", 600),
        ("2h", 7200),
        ("1d", 86400),
        ("1h30m", 5400),
        ("2д", 2 * 86400),
        ("10хв", 600),
        ("30с", 30),
        ("1год", 3600),
        ("15", 15 * 60),        # голе число = хвилини
        ("2h 30m", 9000),
    ],
)
def test_parse_duration_ok(raw, expected):
    assert parse_duration(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "хтозна", "abc", None])
def test_parse_duration_bad(raw):
    assert parse_duration(raw) is None


def test_parse_duration_case_insensitive():
    assert parse_duration("2H") == 7200
    assert parse_duration("10ХВ") == 600


# ---------------- fix_tables ----------------

def test_fix_tables_converts_pipe_table():
    table = (
        "| Агент | Елемент |\n"
        "|-------|---------|\n"
        "| Miyabi | Frost |\n"
        "| Yanagi | Electric |\n"
    )
    out = fix_tables(table)
    assert "|" not in out
    assert "**Miyabi**" in out
    assert "Frost" in out
    assert "**Yanagi**" in out


def test_fix_tables_leaves_code_block_untouched():
    text = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```"
    assert fix_tables(text) == text


def test_fix_tables_non_table_pipes_ignored():
    # один рядок без роздільника — не таблиця
    text = "| просто рядок з пайпом |"
    assert fix_tables(text) == text


def test_fix_tables_preserves_surrounding_text():
    text = "Ось порівняння:\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nКінець."
    out = fix_tables(text)
    assert out.startswith("Ось порівняння:")
    assert out.rstrip().endswith("Кінець.")
    assert "|" not in out


def test_fix_tables_no_table_is_identity():
    text = "Звичайний текст без таблиць.\nДругий рядок."
    assert fix_tables(text) == text


# ---------------- looks_ukrainian ----------------

def test_looks_ukrainian_true_on_ukrainian():
    text = "Їжак їздив містом, і їй було цікаво, чи є ще їстівне."
    assert looks_ukrainian(text) is True


def test_looks_ukrainian_false_on_russian():
    text = "Ёжик ездил по городу, и ему было интересно, есть ли ещё что-то съедобное."
    assert looks_ukrainian(text) is False


def test_looks_ukrainian_below_threshold():
    # поодинокі і/ї не мають спрацьовувати
    assert looks_ukrainian("текст із двома і", threshold=6) is False


def test_looks_ukrainian_empty():
    assert looks_ukrainian("") is False
    assert looks_ukrainian(None) is False


def test_looks_ukrainian_custom_threshold():
    assert looks_ukrainian("їіїі", threshold=4) is True
    assert looks_ukrainian("їіїі", threshold=5) is False


# ---------------- roll_dice ----------------

def test_roll_dice_basic():
    random.seed(1)
    rolls, modifier, total = roll_dice("2d6")
    assert len(rolls) == 2
    assert modifier == 0
    assert total == sum(rolls)
    assert all(1 <= r <= 6 for r in rolls)


def test_roll_dice_with_modifier():
    rolls, modifier, total = roll_dice("1d20+3")
    assert len(rolls) == 1
    assert modifier == 3
    assert total == rolls[0] + 3


def test_roll_dice_negative_modifier():
    rolls, modifier, total = roll_dice("1d20-2")
    assert modifier == -2
    assert total == rolls[0] - 2


def test_roll_dice_cyrillic_d():
    rolls, _, _ = roll_dice("3д8")
    assert len(rolls) == 3
    assert all(1 <= r <= 8 for r in rolls)


def test_roll_dice_default_count_is_one():
    rolls, _, _ = roll_dice("d20")
    assert len(rolls) == 1


@pytest.mark.parametrize("formula", ["", "abc", "0d6", "101d6", "1d1", "1d1001"])
def test_roll_dice_invalid(formula):
    with pytest.raises(ValueError):
        roll_dice(formula)
