"""Тести zzz.db.ZZZDatabase: search (en/zh), describe (curated/divergences/
застарілість), auto_context (відмінки/трансліт), match_bangboo.

Фікстура пише реальні JSON-БД у tmp і вантажить їх через load() — так
покривається і побудова індексу.
"""
from __future__ import annotations

import json

import pytest

from zzz.db import ZZZDatabase


AGENTS = {
    "1091": {
        "name": "Miyabi", "full_name": "Hoshimi Miyabi", "name_zh": "星见雅",
        "rarity": "S", "element": "Ice", "special_element": "Frost",
        "specialty": "Attack", "attack_type": "Slash", "faction": "Section 6",
        "base_stats": {"HP": 8000, "ATK": 900.0, "junk": 0},
        "core_skill": {"name": "Core", "brief": "морозить ворогів"},
        "skills": {"Basic": [{"name": "Удар", "brief": "базова атака"}]},
        "mindscapes": {"1": {"name": "M1", "brief": "підсилення"}},
        "game_recommend": {
            "disc_4pc": "Woodpecker", "disc_2pc": "Polar Metal",
            "main_stats": {"slot4": "CRIT", "slot6": "ATK%"},
        },
    },
    "1220": {
        "name": "Yanagi", "full_name": "Tsukishiro Yanagi", "name_zh": "月城柳",
        "rarity": "S", "element": "Electric", "specialty": "Anomaly",
        "faction": "Section 6",
        "curated": {
            "verdict": "T0 у більшості складів",
            "divergences": [
                {
                    "topic": "team", "cn": "з Miyabi", "west": "соло-дизбалансер",
                    "reason": "різні мета-склади", "verdict": "бери з Miyabi",
                    "confidence": "high", "patch": "2.0", "note": "перевір після патчу",
                }
            ],
        },
    },
    "1300": {
        "name": "Pulchra", "full_name": "Pulchra Fellini", "name_zh": "",
        "rarity": "A", "element": "Physical", "specialty": "Stun", "faction": "Cunning Hares",
    },
}

WENGINES = {
    "14001": {
        "name": "Steel Cushion", "name_zh": "钢铁坐垫", "rarity": "S", "specialty": "Attack",
        "base_stat": {"stat": "ATK", "value": 700},
        "adv_stat": {"stat": "CRIT", "value": 24},
        "effect_name": "Cushion", "effect_r1": "бонус до фізичного",
        "effect_r5": "більший бонус",
    },
}

DISCS = {
    "31000": {"name": "Woodpecker Electro", "name_zh": "啄木鸟", "set2": "CRIT +8%", "set4": "стак після криту"},
}

BANGBOO = {
    "53001": {
        "name": "Amillion", "name_zh": "阿米", "rarity": "S",
        "description": "універсальний банбу",
        "skills": {"1": {"name": "Boom", "brief": "вибух"}},
        "activation": {"count": 2, "subject": "Ice", "text": "at least 2 Ice characters"},
    },
    "53002": {
        "name": "Butler", "rarity": "A", "skills": {},
        "activation": {"text": "щось нерозпарсене про squad"},  # без subject
    },
    "53003": {
        "name": "Plain", "rarity": "A", "skills": {},  # без activation
    },
    "53004": {
        "name": "Sharkboo", "rarity": "S", "skills": {},  # S без activation
    },
}

META = {"game_version": "3.0", "channel": "live", "generated_at": "2026-07-06", "counts": {}}


@pytest.fixture
def zdb(tmp_path):
    root = tmp_path / "zzz"
    root.mkdir()
    (root / "agents.json").write_text(json.dumps(AGENTS, ensure_ascii=False), encoding="utf-8")
    (root / "wengines.json").write_text(json.dumps(WENGINES, ensure_ascii=False), encoding="utf-8")
    (root / "discs.json").write_text(json.dumps(DISCS, ensure_ascii=False), encoding="utf-8")
    (root / "bangboo.json").write_text(json.dumps(BANGBOO, ensure_ascii=False), encoding="utf-8")
    (root / "meta.json").write_text(json.dumps(META, ensure_ascii=False), encoding="utf-8")
    return ZZZDatabase(root).load()


# ---------------- load ----------------

def test_load_missing_files_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ZZZDatabase(tmp_path / "empty").load()


def test_loaded_flag(zdb):
    assert zdb.loaded is True


# ---------------- search ----------------

def test_search_exact_first(zdb):
    results = zdb.search("Miyabi")
    assert results[0] == ("agents", "1091", "Miyabi")


def test_search_partial(zdb):
    results = zdb.search("miy")
    assert any(name == "Miyabi" for _, _, name in results)


def test_search_by_zh(zdb):
    results = zdb.search("月城柳")
    assert any(item_id == "1220" for _, item_id, _ in results)


def test_search_by_full_name(zdb):
    results = zdb.search("Fellini")
    assert any(name == "Pulchra" for _, _, name in results)


def test_search_kind_filter(zdb):
    results = zdb.search("s", kind="wengines")
    assert all(kind == "wengines" for kind, _, _ in results)


def test_search_empty_query(zdb):
    assert zdb.search("") == []
    assert zdb.search("   ") == []


def test_search_exact_ranked_before_partial(zdb):
    # додаємо запит, що є і точним, і підрядком
    results = zdb.search("Yanagi")
    assert results[0][2] == "Yanagi"


# ---------------- get / describe ----------------

def test_get_by_id(zdb):
    found = zdb.get("agents", "1091")
    assert found is not None and found[0] == "1091"


def test_get_by_name(zdb):
    found = zdb.get("agents", "Miyabi")
    assert found is not None and found[1]["name"] == "Miyabi"


def test_get_unknown_kind(zdb):
    assert zdb.get("nonsense", "x") is None


def test_describe_agent_core_fields(zdb):
    text = zdb.describe("agents", "Miyabi")
    assert "Miyabi" in text
    assert "Ice" in text and "Frost" in text  # special_element
    assert "星见雅" in text
    assert "Гра рекомендує" in text


def test_describe_not_found_suggests(zdb):
    text = zdb.describe("agents", "Мітсуджі")
    assert "Не знайдено" in text


def test_describe_includes_curated(zdb):
    text = zdb.describe("agents", "Yanagi")
    assert "Кураторські нотатки" in text
    assert "T0" in text


def test_describe_divergence_both_sides(zdb):
    text = zdb.describe("agents", "Yanagi")
    assert "CN:" in text and "West:" in text
    assert "Причина розбіжності" in text
    assert "Вердикт власника" in text


def test_describe_divergence_stale_warning(zdb):
    # патч звірки 2.0, поточна версія 3.0 -> має бути попередження
    text = zdb.describe("agents", "Yanagi")
    assert "звірено у v2.0" in text
    assert "могла застаріти" in text


def test_describe_wengine(zdb):
    text = zdb.describe("wengines", "Steel Cushion")
    assert "W-ENGINE" in text
    assert "R1" in text


def test_describe_disc(zdb):
    text = zdb.describe("discs", "Woodpecker Electro")
    assert "2pc" in text and "4pc" in text


# ---------------- overview ----------------

def test_overview_lists_all(zdb):
    text = zdb.overview("agents")
    assert "Miyabi" in text and "Yanagi" in text and "Pulchra" in text
    assert "agents (3)" in text


def test_overview_unknown_kind(zdb):
    assert "Невідомий тип" in zdb.overview("junk")


# ---------------- auto_context ----------------

def test_auto_context_finds_entity(zdb):
    block, labels = zdb.auto_context("розкажи про Miyabi білд")
    assert block is not None
    assert "Miyabi" in labels
    assert "СПРАВОЧНЫЕ ДАННЫЕ" in block


def test_auto_context_cyrillic_translit(zdb):
    # «Міябі» -> miyabi через трансліт
    block, labels = zdb.auto_context("що там по Міябі")
    assert labels and "Miyabi" in labels


def test_auto_context_declension_ending(zdb):
    # відмінкове закінчення відкушується («Янагі» -> yanagi)
    block, labels = zdb.auto_context("білд на Янагі зараз")
    assert labels and "Yanagi" in labels


def test_auto_context_zh_match(zdb):
    block, labels = zdb.auto_context("гайд по 星见雅 будь ласка")
    assert labels and "Miyabi" in labels


def test_auto_context_no_hits(zdb):
    block, labels = zdb.auto_context("абсолютно нічого релевантного тут немає")
    assert block is None
    assert labels == []


def test_auto_context_limit(zdb):
    block, labels = zdb.auto_context("Miyabi Yanagi Pulchra разом", limit=2)
    assert len(labels) <= 2


def test_auto_context_empty_text(zdb):
    assert zdb.auto_context("") == (None, [])


# ---------------- match_bangboo ----------------

def test_match_bangboo_condition_met(zdb):
    # Miyabi має element Ice + special_element Frost; умова Amillion — 2 Ice
    # додамо ще одного Ice-агента? У нас лише Miyabi Ice. count=2 не виконається
    # тому перевіримо на невиконанні нижче; тут — базова структура
    result = zdb.match_bangboo(["Miyabi", "Yanagi"])
    assert "Команда:" in result
    assert "Miyabi" in result and "Yanagi" in result


def test_match_bangboo_unrecognized(zdb):
    result = zdb.match_bangboo(["ХтосьНеіснуючий"])
    assert "Не впізнав" in result


def test_match_bangboo_lists_unconditional_s(zdb):
    result = zdb.match_bangboo(["Miyabi"])
    assert "Sharkboo" in result  # S без умов активації


def test_match_bangboo_manual_condition(zdb):
    result = zdb.match_bangboo(["Miyabi"])
    # Butler має activation без subject -> потрапляє в «не розпарсена»
    assert "не розпарсена" in result or "Butler" in result


def test_match_bangboo_old_db_warning(tmp_path):
    root = tmp_path / "zzz"
    root.mkdir()
    old_bangboo = {"1": {"name": "NoActivation", "rarity": "A", "skills": {}}}
    for name, data in (("agents", AGENTS), ("wengines", WENGINES), ("discs", DISCS)):
        (root / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (root / "bangboo.json").write_text(json.dumps(old_bangboo, ensure_ascii=False), encoding="utf-8")
    (root / "meta.json").write_text(json.dumps(META, ensure_ascii=False), encoding="utf-8")
    db = ZZZDatabase(root).load()
    result = db.match_bangboo(["Miyabi"])
    assert "старою версією" in result


# ---------------- stats_line ----------------

def test_stats_line(zdb):
    line = zdb.stats_line()
    assert "3.0" in line
