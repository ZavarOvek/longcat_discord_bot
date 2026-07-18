"""Тести zzz.build_db: чисті трансформації (clean/brief/prop_name), парсер умов
активації, _sanitize_agent_raw, _extract_game_recommend, record-трансформери
на легких фікстурах, merge_curated з валідацією divergences.

hakushin застаблено в conftest.py, тож import build_db не тягне реальний пакет.
Record-трансформери приймають будь-які об'єкти з потрібними атрибутами —
використовуємо SimpleNamespace замість pydantic-моделей обгортки.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from zzz import build_db as bd


# ---------------- clean / brief / prop_name ----------------

def test_clean_strips_tags_and_whitespace():
    assert bd.clean("<color=#fff>Привіт</color>   світ") == "Привіт світ"


def test_clean_handles_newlines():
    assert bd.clean("рядок\\nдругий\nтретій") == "рядок другий третій"


def test_clean_none():
    assert bd.clean(None) == ""


def test_brief_short_untouched():
    assert bd.brief("коротко", 100) == "коротко"


def test_brief_cuts_on_sentence():
    # межа речення після середини ліміту -> ріже по крапці
    text = "х" * 30 + ". " + "у" * 200
    out = bd.brief(text, 50)
    assert out.endswith(".")
    assert len(out) <= 51


def test_brief_cuts_on_word_with_ellipsis():
    text = "слово " * 100
    out = bd.brief(text, 40)
    assert out.endswith("…")


def test_prop_name_from_object():
    assert bd.prop_name(SimpleNamespace(name="Ice")) == "Ice"


def test_prop_name_none():
    assert bd.prop_name(None) is None


def test_prop_name_fallback_str():
    assert bd.prop_name(42) == "42"


# ---------------- _lite (сирий список) ----------------

def test_lite_maps_rank_to_rarity():
    obj = bd._lite("1091", {"code": "Miyabi", "rank": 4, "names": {"en": "Miyabi"}, "zh": {"name": "雅"}})
    assert obj.rarity == "S"
    assert obj.id == 1091
    assert obj.names.get("zh") == "雅"


def test_lite_preserves_string_rank():
    obj = bd._lite("1", {"name": "X", "rank": "A"})
    assert obj.rarity == "A"


def test_lite_fallback_name():
    obj = bd._lite("999", {})
    assert obj.name == "999"


# ---------------- _sanitize_agent_raw ----------------

def test_sanitize_fills_missing_partner_fields():
    raw = {"partner_info": {"full_name": "Hoshimi"}}
    out = bd._sanitize_agent_raw(raw)
    partner = out["partner_info"]
    # відсутні обов'язкові поля заповнені
    for field in ("birthday", "gender", "outlook_desc", "race", "unlock_condition"):
        assert field in partner
    # наявне поле збережене
    assert partner["full_name"] == "Hoshimi"


def test_sanitize_preserves_new_fields():
    raw = {"partner_info": {"new_field_3_0": "значення"}}
    out = bd._sanitize_agent_raw(raw)
    assert out["partner_info"]["new_field_3_0"] == "значення"


def test_sanitize_no_partner_info():
    raw = {"other": 1}
    assert bd._sanitize_agent_raw(raw) == {"other": 1}


# ---------------- _extract_game_recommend ----------------

def test_extract_game_recommend_full():
    fairy = {
        "slot4": "31000", "slot2": "32000", "slot_sub": "33000",
        "part4": {"name": "CRIT DMG"}, "part5": {"name": "ATK%"},
        "part6": {"name": "ATK%"}, "part_sub": {"name": "CRIT Rate"},
    }
    rec = bd._extract_game_recommend(fairy)
    assert rec["disc_4pc_id"] == "31000"
    assert rec["disc_2pc_id"] == "32000"
    assert rec["disc_alt_id"] == "33000"
    assert rec["main_stats"]["slot4"] == "CRIT DMG"
    assert rec["main_stats"]["substat"] == "CRIT Rate"


def test_extract_game_recommend_empty_returns_none():
    assert bd._extract_game_recommend({}) is None


def test_extract_game_recommend_non_dict():
    assert bd._extract_game_recommend(None) is None


# ---------------- _parse_activation ----------------

def test_parse_activation_type_pattern():
    parsed = bd._parse_activation("When there are at least 2 Ice characters in your squad")
    assert parsed["count"] == 2
    assert parsed["subject"] == "Ice"


def test_parse_activation_from_pattern():
    parsed = bd._parse_activation("at least 3 characters from Section 6 in the squad")
    assert parsed["count"] == 3
    assert "Section 6" in parsed["subject"]


def test_parse_activation_strips_attribute_suffix():
    parsed = bd._parse_activation("at least 2 Electric Attribute characters in your squad")
    assert parsed["subject"] == "Electric"


def test_parse_activation_unparsed_keeps_text():
    parsed = bd._parse_activation("at least something vague about the squad here")
    assert parsed is not None
    assert "subject" not in parsed
    assert "text" in parsed


def test_parse_activation_none_for_irrelevant():
    assert bd._parse_activation("Deals extra damage on hit.") is None
    assert bd._parse_activation("") is None
    assert bd._parse_activation(None) is None


# ---------------- record-трансформери ----------------

def _agent_payload():
    detail = SimpleNamespace(
        name="Miyabi",
        info=SimpleNamespace(full_name="Hoshimi Miyabi"),
        rarity="S",
        element=SimpleNamespace(name="Ice"),
        specialty=SimpleNamespace(name="Attack"),
        attack_type=SimpleNamespace(name="Slash"),
        faction=SimpleNamespace(name="Section 6"),
        stats={"HP": 8000, "ATK": 900.0, "tags": "junk", "zero": 0},
        passive=SimpleNamespace(levels={
            1: SimpleNamespace(names=["Core Passive"], descriptions=["морозить <color=#f>ворогів</color>"])
        }),
        skills={
            "Basic": SimpleNamespace(descriptions=[
                SimpleNamespace(name="Удар", description="базова атака"),
            ]),
        },
        mindscape_cinemas=[SimpleNamespace(level=1, name="M1", description="підсилення")],
    )
    extras = {"special_element": "Frost", "strategy": ["агресивний"], "game_recommend": {"disc_4pc_id": "31000"}}
    return SimpleNamespace(detail=detail, extras=extras)


def test_agent_record_core_fields():
    item = SimpleNamespace(names={"zh": "星见雅"})
    record = bd.agent_record(item, _agent_payload())
    assert record["name"] == "Miyabi"
    assert record["full_name"] == "Hoshimi Miyabi"
    assert record["name_zh"] == "星见雅"
    assert record["element"] == "Ice"
    assert record["special_element"] == "Frost"


def test_agent_record_stats_junk_filtered():
    item = SimpleNamespace(names={})
    record = bd.agent_record(item, _agent_payload())
    assert "tags" not in record["base_stats"]  # у _STATS_JUNK
    assert "zero" not in record["base_stats"]  # нульові відкидаються
    assert record["base_stats"]["HP"] == 8000


def test_agent_record_skills_cleaned():
    item = SimpleNamespace(names={})
    record = bd.agent_record(item, _agent_payload())
    assert "Basic" in record["skills"]
    assert record["skills"]["Basic"][0]["brief"] == "базова атака"


def test_agent_record_core_skill():
    item = SimpleNamespace(names={})
    record = bd.agent_record(item, _agent_payload())
    assert record["core_skill"]["name"] == "Core Passive"
    assert "морозить" in record["core_skill"]["brief"]


def test_wengine_record():
    item = SimpleNamespace(names={"zh": "钢铁"})
    detail = SimpleNamespace(
        name="Steel Cushion", rarity="S",
        type=SimpleNamespace(name="Attack"),
        base_property=SimpleNamespace(name="ATK", value=700),
        rand_property=SimpleNamespace(name="CRIT", value=24),
        refinements={"1": SimpleNamespace(name="Cushion", description="ефект R1"),
                     "5": SimpleNamespace(name="Cushion", description="ефект R5")},
    )
    record = bd.wengine_record(item, detail)
    assert record["name"] == "Steel Cushion"
    assert record["base_stat"]["stat"] == "ATK"
    assert record["effect_r1"] == "ефект R1"
    assert record["effect_r5"] == "ефект R5"


def test_disc_record():
    item = SimpleNamespace(chs_info=SimpleNamespace(name="啄木鸟"))
    detail = SimpleNamespace(
        name="Woodpecker", two_piece_effect="CRIT +8%", four_piece_effect="стак після криту",
    )
    record = bd.disc_record(item, detail)
    assert record["name"] == "Woodpecker"
    assert record["name_zh"] == "啄木鸟"
    assert "CRIT" in record["set2"]


def test_bangboo_record_with_activation():
    item = SimpleNamespace(names={"zh": "阿米"})
    detail = SimpleNamespace(
        name="Amillion", rarity="S", description="універсал",
        skills={"1": {2: SimpleNamespace(
            name="Boom",
            description="When there are at least 2 Ice characters in your squad, boom.",
        )}},
    )
    record = bd.bangboo_record(item, detail)
    assert record["name"] == "Amillion"
    assert record["activation"]["count"] == 2
    assert record["activation"]["subject"] == "Ice"


# ---------------- merge_curated + валідація ----------------

def test_merge_curated_attaches(tmp_path):
    db = {"1091": {"name": "Miyabi"}}
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"1091": {"verdict": "T0"}}, ensure_ascii=False), encoding="utf-8")
    bd.merge_curated(db, path, "agents")
    assert db["1091"]["curated"] == {"verdict": "T0"}


def test_merge_curated_missing_file_noop(tmp_path):
    db = {"1": {"name": "x"}}
    bd.merge_curated(db, tmp_path / "nope.json", "agents")
    assert "curated" not in db["1"]


def test_merge_curated_unknown_id_warns(tmp_path, caplog):
    db = {"1091": {"name": "Miyabi"}}
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"9999": {"verdict": "?"}}, ensure_ascii=False), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        bd.merge_curated(db, path, "agents")
    assert "немає в свіжих даних" in caplog.text


def test_validate_divergences_missing_fields_warn(tmp_path, caplog):
    db = {"1220": {"name": "Yanagi"}}
    overlay = {"1220": {"divergences": [{"topic": "team"}]}}  # бракує cn/west
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        bd.merge_curated(db, path, "agents")
    assert "бракує обов'язкових полів" in caplog.text


def test_validate_divergences_unknown_topic_warn(tmp_path, caplog):
    db = {"1220": {"name": "Yanagi"}}
    overlay = {"1220": {"divergences": [{"topic": "погода", "cn": "a", "west": "b"}]}}
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        bd.merge_curated(db, path, "agents")
    assert "невідомий topic" in caplog.text


def test_validate_divergences_missing_patch_warn(tmp_path, caplog):
    db = {"1220": {"name": "Yanagi"}}
    overlay = {"1220": {"divergences": [{"topic": "team", "cn": "a", "west": "b"}]}}  # без patch
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        bd.merge_curated(db, path, "agents")
    assert "нема поля patch" in caplog.text


def test_validate_divergences_not_a_list_warn(tmp_path, caplog):
    db = {"1220": {"name": "Yanagi"}}
    overlay = {"1220": {"divergences": "не список"}}
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        bd.merge_curated(db, path, "agents")
    assert "має бути списком" in caplog.text
