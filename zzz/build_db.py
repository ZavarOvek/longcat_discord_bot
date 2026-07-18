"""Генератор ZZZ-баз: тягне живі дані з hakush.in (через hakushin-py)
і збирає чотири компактні JSON-БД для Discord-бота:

    data/zzz/agents.json    — агенти (статки, кит, майндскейпи)
    data/zzz/wengines.json  — W-Engines (базова/просунута статка, ефекти R1/R5)
    data/zzz/discs.json     — Drive Discs (2pc/4pc ефекти)
    data/zzz/bangboo.json   — банбу (навички)
    data/zzz/meta.json      — версія гри, дата генерації, лічильники

Запуск:  python -m zzz.build_db            (стабільна версія гри)
         python -m zzz.build_db --beta     (включно з бета-даними)
         python -m zzz.build_db --fresh    (ігнорувати кеш обгортки)

Після кожного патча ZZZ просто перезапусти скрипт — дані оновляться самі.
Ручні нотатки (тіри, F2P-альтернативи) живуть окремо в data/zzz/curated/*.json
і мерджаться в записи під ключем "curated", тож регенерація їх не стирає.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import hakushin
from hakushin import Game, Language
from hakushin.models import zzz as zzz_models

log = logging.getLogger("zzz.build_db")

CONCURRENCY = 4          # ввічлива паралельність до API
BRIEF_SKILL = 350        # ліміти обрізання описів, символів
BRIEF_MINDSCAPE = 220
BRIEF_EFFECT = 450

_TAG_RE = re.compile(r"<[^<>]{1,60}>")   # <color=...>, <IconMap:...> і подібне
_WS_RE = re.compile(r"\s+")


def clean(text: str | None) -> str:
    """Прибирає розмітку рушія і зайві пробіли з ігрового тексту."""
    if not text:
        return ""
    text = _TAG_RE.sub("", text.replace("\\n", " ").replace("\n", " "))
    return _WS_RE.sub(" ", text).strip()


def brief(text: str | None, limit: int) -> str:
    """clean() + м'яке обрізання по межі речення/слова."""
    text = clean(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = cut.rfind(". ")
    if dot > limit // 2:
        return cut[: dot + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut) + "…"


def prop_name(prop: Any) -> str | None:
    """CharacterProp/enum/будь-що -> людська назва."""
    if prop is None:
        return None
    name = getattr(prop, "name", None)
    return name if isinstance(name, str) else str(prop)


# ---------------- стійкий фетч списків ----------------
# Типізовані list-моделі обгортки мають закриті enum-и (елемент, спеціальність),
# і кожен мажорний патч ZZZ з новим значенням (як element id 204 у 3.0) валить
# валідацію всього списку. Зі списків нам потрібні лише id/name/names/rarity,
# тому читаємо сирий JSON у легкі об'єкти без валідації. Деталі, як і раніше,
# парсяться моделями обгортки — там толерантні CharacterProp (id + name).

_RANK_MAP = {2: "B", 3: "A", 4: "S"}


def _lite(item_id: str, raw: dict) -> SimpleNamespace:
    en_info = raw.get("en") if isinstance(raw.get("en"), dict) else {}
    zh_info = raw.get("zh") if isinstance(raw.get("zh"), dict) else {}
    names = dict(raw.get("names")) if isinstance(raw.get("names"), dict) else {}
    if not names.get("zh") and zh_info.get("name"):
        names["zh"] = zh_info["name"]
    name = raw.get("code") or raw.get("name") or en_info.get("name") or str(item_id)
    rank = raw.get("rank")
    rarity = _RANK_MAP.get(rank) if isinstance(rank, int) else rank
    return SimpleNamespace(
        id=int(item_id),
        name=name,
        names=names,
        rarity=rarity,
        chs_info=SimpleNamespace(name=zh_info["name"]) if zh_info.get("name") else None,
    )


async def _fetch_list_raw(client: Any, endpoint: str, *, fresh: bool) -> list[SimpleNamespace]:
    data = await client._request(endpoint, use_cache=not fresh, in_data=True)  # noqa: SLF001
    return [_lite(item_id, raw) for item_id, raw in data.items() if isinstance(raw, dict)]


# ---------------- толерантний фетч деталей агентів ----------------
# Патч 3.0 перекроїв partner_info (зникли outlook_desc/race) — модель обгортки
# на цьому падає. Нормалізуємо сирий JSON перед валідацією: заповнюємо відсутні
# обов'язкові поля порожніми значеннями, а все нове пропускаємо як є.
# Заодно з сирих даних забираємо те, чого обгортка не віддає: special_element,
# strategy і fairy_recommend (рекомендації дисків/статів від самої гри).

_PARTNER_REQUIRED: dict[str, Any] = {
    "birthday": "",
    "full_name": "",
    "gender": "",
    "impression_f": "",
    "impression_m": "",
    "outlook_desc": "",
    "profile_desc": "",
    "race": "",
    "unlock_condition": [],
}

_STATS_JUNK = {"avatar_piece_id", "tags", "rbl", "rbl_correction_factor", "rbl_probability"}


def _sanitize_agent_raw(raw: dict) -> dict:
    partner = raw.get("partner_info")
    if isinstance(partner, dict):
        raw["partner_info"] = {**_PARTNER_REQUIRED, **partner}
    return raw


def _extract_game_recommend(fairy: Any) -> dict | None:
    """fairy_recommend -> {'disc_4pc_id', 'disc_2pc_id', 'disc_alt_id', 'main_stats'}."""
    if not isinstance(fairy, dict):
        return None
    rec: dict[str, Any] = {}
    for src, dst in (("slot4", "disc_4pc_id"), ("slot2", "disc_2pc_id"), ("slot_sub", "disc_alt_id")):
        if fairy.get(src):
            rec[dst] = fairy[src]
    stats: dict[str, str] = {}
    for src, dst in (("part4", "slot4"), ("part5", "slot5"), ("part6", "slot6"), ("part_sub", "substat")):
        part = fairy.get(src)
        name = part.get("name") if isinstance(part, dict) else None
        if name:
            stats[dst] = name
    if stats:
        rec["main_stats"] = stats
    return rec or None


def _make_agent_fetcher(client: Any):
    async def fetch_agent(agent_id: int, *, use_cache: bool = True) -> SimpleNamespace:
        raw = await client._request(f"character/{agent_id}", use_cache=use_cache)  # noqa: SLF001
        raw = _sanitize_agent_raw(raw)
        detail = zzz_models.CharacterDetail(**raw)
        strategy = [
            clean(s) for s in (raw.get("strategy") or [])
            if isinstance(s, str) and clean(s) and not clean(s).isdigit()
        ]
        special = raw.get("special_element_type")
        extras = {
            "special_element": special.get("name") if isinstance(special, dict) else None,
            "strategy": strategy[:4] or None,
            "game_recommend": _extract_game_recommend(raw.get("fairy_recommend")),
        }
        return SimpleNamespace(detail=detail, extras=extras)

    return fetch_agent


# ---------------- трансформації запис-за-записом ----------------

def agent_record(item: Any, payload: Any) -> dict:
    detail = getattr(payload, "detail", payload)
    extras = getattr(payload, "extras", None) or {}
    names = getattr(item, "names", {}) or {}
    info = detail.info

    core = {}
    passive_levels = getattr(getattr(detail, "passive", None), "levels", None) or {}
    if passive_levels:
        top = passive_levels[max(passive_levels)]
        core = {
            "name": " / ".join(getattr(top, "names", []) or []) or None,
            "brief": brief(" ".join(getattr(top, "descriptions", []) or []), BRIEF_SKILL),
        }

    skills: dict[str, list[dict]] = {}
    for skill_type, skill in (detail.skills or {}).items():
        moves = []
        for desc in getattr(skill, "descriptions", []) or []:
            if getattr(desc, "description", None):
                moves.append({"name": desc.name, "brief": brief(desc.description, BRIEF_SKILL)})
            if len(moves) >= 3:
                break
        if moves:
            skills[prop_name(skill_type)] = moves

    mindscapes = {
        str(cinema.level): {"name": cinema.name, "brief": brief(cinema.description, BRIEF_MINDSCAPE)}
        for cinema in (detail.mindscape_cinemas or [])
    }

    record = {
        "name": detail.name,
        "full_name": getattr(info, "full_name", None) if info else None,
        "name_zh": names.get("zh"),
        "rarity": detail.rarity,
        "element": prop_name(detail.element),
        "specialty": prop_name(detail.specialty),
        "attack_type": prop_name(detail.attack_type),
        "faction": prop_name(detail.faction),
        "base_stats": {
            k: v
            for k, v in (detail.stats or {}).items()
            if isinstance(v, (int, float)) and v and k not in _STATS_JUNK
        },
        "core_skill": core or None,
        "skills": skills,
        "mindscapes": mindscapes,
    }
    if extras.get("special_element"):
        record["special_element"] = extras["special_element"]
    if extras.get("strategy"):
        record["strategy"] = extras["strategy"]
    if extras.get("game_recommend"):
        record["game_recommend"] = extras["game_recommend"]
    return record


def wengine_record(item: Any, detail: Any) -> dict:
    names = getattr(item, "names", {}) or {}
    refinements = detail.refinements or {}
    r1 = refinements.get("1")
    r5 = refinements.get("5") or (refinements[max(refinements)] if refinements else None)

    def stat(prop: Any) -> dict | None:
        if prop is None:
            return None
        return {"stat": clean(getattr(prop, "name", "")) or None, "value": getattr(prop, "value", None)}

    return {
        "name": detail.name,
        "name_zh": names.get("zh"),
        "rarity": detail.rarity,
        "specialty": prop_name(getattr(detail, "type", None)),
        "base_stat": stat(getattr(detail, "base_property", None)),
        "adv_stat": stat(getattr(detail, "rand_property", None)),
        "effect_name": getattr(r1, "name", None),
        "effect_r1": brief(getattr(r1, "description", None), BRIEF_EFFECT) or None,
        "effect_r5": brief(getattr(r5, "description", None), BRIEF_EFFECT) or None,
    }


def disc_record(item: Any, detail: Any) -> dict:
    chs = getattr(item, "chs_info", None)
    return {
        "name": detail.name,
        "name_zh": getattr(chs, "name", None) if chs else None,
        "set2": brief(detail.two_piece_effect, BRIEF_EFFECT) or None,
        "set4": brief(detail.four_piece_effect, BRIEF_EFFECT) or None,
    }


# --- умови активації банбу: у даних лише текст, парсимо в структуру ---
_COND_FROM_RE = re.compile(
    r"at least (\d+)\s+characters?\s+(?:from|of)\s+(.+?)\s+in (?:your|the) squad", re.IGNORECASE
)
_COND_TYPE_RE = re.compile(
    r"at least (\d+)\s+(.+?)\s+characters?\s+in (?:your|the) squad", re.IGNORECASE
)


def _parse_activation(text: str | None) -> dict | None:
    """«When there's at least 2 Ice Attribute characters in your squad» ->
    {"count": 2, "subject": "Ice", "text": ...}. Якщо умова є, але не
    розпізналась — лишаємо тільки text (модель оцінить сама)."""
    cleaned = clean(text)
    if not cleaned:
        return None
    match = _COND_FROM_RE.search(cleaned) or _COND_TYPE_RE.search(cleaned)
    if match:
        subject = re.sub(r"\s*attributes?$", "", match.group(2).strip(), flags=re.IGNORECASE).strip()
        return {"count": int(match.group(1)), "subject": subject, "text": cleaned[:200]}
    lowered = cleaned.lower()
    if "at least" in lowered and "squad" in lowered:
        return {"text": cleaned[:200]}
    return None


def bangboo_record(item: Any, detail: Any) -> dict:
    names = getattr(item, "names", {}) or {}
    skills = {}
    activation: dict | None = None
    for slot, by_level in (detail.skills or {}).items():
        if not by_level:
            continue
        top = by_level[max(by_level)]
        skills[str(slot)] = {"name": top.name, "brief": brief(top.description, BRIEF_SKILL)}
        parsed = _parse_activation(getattr(top, "description", None))
        if parsed and (activation is None or ("subject" in parsed and "subject" not in activation)):
            activation = parsed
    record = {
        "name": detail.name,
        "name_zh": names.get("zh"),
        "rarity": detail.rarity,
        "description": brief(getattr(detail, "description", None), BRIEF_MINDSCAPE) or None,
        "skills": skills,
    }
    if activation:
        record["activation"] = activation
    return record


# ---------------- конвеєр ----------------

async def _fetch_details(items, fetch_fn, make_record, *, fresh: bool, label: str) -> dict[str, dict]:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    total = len(items)
    result: dict[str, dict] = {}
    done = 0

    async def one(item):
        nonlocal done
        async with semaphore:
            try:
                detail = await fetch_fn(item.id, use_cache=not fresh)
                result[str(item.id)] = make_record(item, detail)
            except Exception as exc:  # noqa: BLE001 — пропускаємо одиничні збої
                log.warning("%s %s (%s): %s", label, item.id, getattr(item, "name", "?"), exc)
            done += 1
            if done % 10 == 0 or done == total:
                log.info("%s: %d/%d", label, done, total)

    await asyncio.gather(*(one(item) for item in items))
    return dict(sorted(result.items(), key=lambda kv: int(kv[0])))


_DIVERGENCE_REQUIRED = ("topic", "cn", "west")
_DIVERGENCE_TOPICS = {"discs", "wengine", "stats", "mindscapes", "team", "rotation", "tier"}


def _validate_divergences(overlay: dict, label: str) -> None:
    """М'яка перевірка формату CN/West-розбіжностей: попереджає, не валить."""
    for key, notes in overlay.items():
        divergences = notes.get("divergences") if isinstance(notes, dict) else None
        if divergences is None:
            continue
        if not isinstance(divergences, list):
            log.warning("curated/%s %s: divergences має бути списком", label, key)
            continue
        for index, item in enumerate(divergences):
            where = f"curated/{label} {key} divergences[{index}]"
            if not isinstance(item, dict):
                log.warning("%s: очікував об'єкт", where)
                continue
            missing = [field for field in _DIVERGENCE_REQUIRED if not item.get(field)]
            if missing:
                log.warning("%s: бракує обов'язкових полів %s", where, missing)
            topic = item.get("topic")
            if topic and topic not in _DIVERGENCE_TOPICS:
                log.warning(
                    "%s: невідомий topic «%s» (відомі: %s)", where, topic, ", ".join(sorted(_DIVERGENCE_TOPICS))
                )
            if not item.get("patch"):
                log.warning("%s: нема поля patch — не зможу попереджати про застарілість звірки", where)


def merge_curated(db: dict[str, dict], curated_path: Path, label: str) -> None:
    if not curated_path.exists():
        return
    overlay = json.loads(curated_path.read_text(encoding="utf-8"))
    _validate_divergences(overlay, label)
    for key, notes in overlay.items():
        if key in db:
            db[key]["curated"] = notes
        else:
            log.warning("curated/%s: id %s немає в свіжих даних (пропущено)", label, key)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("записано %s (%d записів)", path, len(payload) if isinstance(payload, dict) else -1)


async def build(out: Path, lang: Language, *, use_live: bool, fresh: bool) -> None:
    async with hakushin.HakushinAPI(Game.ZZZ, lang, use_live=use_live) as client:
        manifest = await client.fetch_manifest()
        version = manifest.zzz.live_version if use_live else manifest.zzz.latest_version
        log.info("Версія даних ZZZ: %s (%s)", version, "live" if use_live else "latest/beta")

        agents_list = [c for c in await _fetch_list_raw(client, "character", fresh=fresh) if c.rarity]
        wengines_list = [w for w in await _fetch_list_raw(client, "weapon", fresh=fresh) if w.rarity]
        discs_list = await _fetch_list_raw(client, "equipment", fresh=fresh)
        bangboo_list = [b for b in await _fetch_list_raw(client, "bangboo", fresh=fresh) if b.rarity]

        agents = await _fetch_details(
            agents_list, _make_agent_fetcher(client), agent_record, fresh=fresh, label="agents"
        )
        wengines = await _fetch_details(
            wengines_list, client.fetch_weapon_detail, wengine_record, fresh=fresh, label="wengines"
        )
        discs = await _fetch_details(
            discs_list, client.fetch_drive_disc_detail, disc_record, fresh=fresh, label="discs"
        )
        bangboo = await _fetch_details(
            bangboo_list, client.fetch_bangboo_detail, bangboo_record, fresh=fresh, label="bangboo"
        )

    # рекомендації гри посилаються на диски за id — підставляємо людські назви
    for record in agents.values():
        recommend = record.get("game_recommend")
        if not recommend:
            continue
        for id_key, name_key in (
            ("disc_4pc_id", "disc_4pc"),
            ("disc_2pc_id", "disc_2pc"),
            ("disc_alt_id", "disc_alt"),
        ):
            disc = discs.get(str(recommend.get(id_key, "")))
            if disc:
                recommend[name_key] = disc["name"]

    curated_dir = out / "curated"
    for name, db in (("agents", agents), ("wengines", wengines), ("discs", discs), ("bangboo", bangboo)):
        merge_curated(db, curated_dir / f"{name}.json", name)
        write_json(out / f"{name}.json", db)

    write_json(
        out / "meta.json",
        {
            "game_version": str(version),
            "channel": "live" if use_live else "latest",
            "lang": lang.value,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "source": "hakush.in via hakushin-py",
            "counts": {
                "agents": len(agents),
                "wengines": len(wengines),
                "discs": len(discs),
                "bangboo": len(bangboo),
            },
        },
    )
    log.info("Готово: %s", out.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Генератор JSON-БД для ZZZ-режиму бота")
    parser.add_argument("--out", default="data/zzz", help="куди класти БД (типово data/zzz)")
    parser.add_argument("--lang", default="en", help="мова описів: en/zh/ko/ja (типово en)")
    parser.add_argument("--beta", action="store_true", help="брати latest-версію даних (включно з бетою)")
    parser.add_argument("--fresh", action="store_true", help="ігнорувати локальний кеш обгортки")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    try:
        lang = Language(args.lang)
    except ValueError:
        parser.error(f"невідома мова: {args.lang}")

    asyncio.run(build(Path(args.out), lang, use_live=not args.beta, fresh=args.fresh))


if __name__ == "__main__":
    main()
