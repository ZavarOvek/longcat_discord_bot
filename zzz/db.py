"""Компактний інтерфейс до ZZZ-баз для LLM-інструментів бота.

Філософія: модель НЕ отримує цілі JSON-файли — вона викликає точкові
запити (search / describe / overview), які повертають короткі текстові
блоки. Так контекст лишається дешевим, а відповіді — точними.

    from zzz.db import ZZZDatabase
    db = ZZZDatabase("data/zzz")
    db.load()
    db.search("miyabi")           -> [("agents", "1091", "Miyabi"), ...]
    db.describe("agents", "1091") -> компактний текстовий блок
    db.overview("wengines")       -> перелік назв з рідкістю
"""
from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from pathlib import Path

KINDS = ("agents", "wengines", "discs", "bangboo")

# --- кирилиця -> латиниця для матчингу імен («Пульхра» -> pulhra -> Pulchra) ---
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "є": "e",
    "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ь": "", "ъ": "",
    "ю": "yu", "я": "ya", "э": "e", "ы": "i", "ё": "e",
}
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ]+")


def _translit(text: str) -> str:
    return "".join(_CYR.get(ch, ch) for ch in text.lower())


def _has_cyrillic(text: str) -> bool:
    return any("а" <= ch <= "я" or ch in "ёіїєґыэ" for ch in text.lower())


class ZZZDatabase:
    def __init__(self, root: str | Path = "data/zzz"):
        self.root = Path(root)
        self._data: dict[str, dict[str, dict]] = {}
        self.meta: dict = {}
        self._name_index: list[tuple[str, str, str, str]] = []  # (lower_name, kind, id, display)
        self._zh_index: list[tuple[str, str, str, str]] = []
        self._name_pool: list[str] = []

    # ---------------- завантаження ----------------

    def load(self) -> "ZZZDatabase":
        missing = [k for k in KINDS if not (self.root / f"{k}.json").exists()]
        if missing:
            raise FileNotFoundError(
                f"Немає файлів {missing} у {self.root}. Спершу згенеруй бази: python -m zzz.build_db"
            )
        for kind in KINDS:
            self._data[kind] = json.loads((self.root / f"{kind}.json").read_text(encoding="utf-8"))
        meta_path = self.root / "meta.json"
        self.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        self._build_index()
        return self

    def _build_index(self) -> None:
        self._name_index = []
        self._zh_index = []
        for kind in KINDS:
            for item_id, record in self._data.get(kind, {}).items():
                display = str(record.get("name") or item_id)
                for field in ("name", "full_name"):
                    value = record.get(field)
                    if value:
                        self._name_index.append((str(value).lower(), kind, item_id, display))
                zh = record.get("name_zh")
                if zh:
                    self._zh_index.append((zh, kind, item_id, display))
        self._name_pool = [entry[0] for entry in self._name_index]

    @property
    def loaded(self) -> bool:
        return bool(self._data)

    # ---------------- пошук ----------------

    def search(self, query: str, kind: str | None = None) -> list[tuple[str, str, str]]:
        """Пошук за підрядком у name / name_zh / full_name (без регістру).
        Повертає [(kind, id, name), ...], точні збіги — першими."""
        needle = (query or "").strip().lower()
        if not needle:
            return []
        kinds = (kind,) if kind in KINDS else KINDS
        exact: list[tuple[str, str, str]] = []
        partial: list[tuple[str, str, str]] = []
        for k in kinds:
            for item_id, record in self._data.get(k, {}).items():
                fields = [
                    str(record.get(field) or "").lower()
                    for field in ("name", "full_name", "name_zh")
                ]
                name = record.get("name", item_id)
                if any(field == needle for field in fields):
                    exact.append((k, item_id, name))
                elif any(field and needle in field for field in fields):
                    partial.append((k, item_id, name))
        return exact + partial

    def get(self, kind: str, key: str) -> tuple[str, dict] | None:
        """Запис за id або назвою. Повертає (id, record) або None."""
        if kind not in KINDS:
            return None
        db = self._data.get(kind, {})
        key = str(key).strip()
        if key in db:
            return key, db[key]
        matches = self.search(key, kind=kind)
        if matches:
            _, item_id, _ = matches[0]
            return item_id, db[item_id]
        return None

    # ---------------- форматування для LLM ----------------

    def describe(self, kind: str, key: str) -> str:
        found = self.get(kind, key)
        if found is None:
            hint = ", ".join(name for _, _, name in self.search(str(key))[:5])
            return f"Не знайдено «{key}» у {kind}." + (f" Схоже: {hint}" if hint else "")
        item_id, record = found
        formatter = getattr(self, f"_format_{kind}")
        text = formatter(item_id, record)
        curated = record.get("curated")
        if curated:
            text += "\n" + self._format_curated(curated)
        return text

    def overview(self, kind: str) -> str:
        if kind not in KINDS:
            return f"Невідомий тип: {kind}. Доступні: {', '.join(KINDS)}"
        lines = [
            f"{record.get('rarity') or '?'} · {record.get('name')} (id {item_id})"
            + (f" · {record['specialty']}" if record.get("specialty") else "")
            + (f" · {record['element']}" if record.get("element") else "")
            for item_id, record in self._data.get(kind, {}).items()
        ]
        return f"{kind} ({len(lines)}):\n" + "\n".join(lines)

    # ---------------- авто-контекст ----------------

    def _best_matches(self, candidate: str) -> list[tuple[float, str, str, str]]:
        """[(score, kind, id, display)] для одного слова/біграми.
        Кирилиця транслітерується; відмінкові закінчення відкушуються (1–2 символи)."""
        norm = candidate.lower().strip()
        if not norm:
            return []
        variants = {norm}
        if _has_cyrillic(norm):
            base = _translit(norm)
            variants = {base}
            for cut in (1, 2):
                if len(base) - cut >= 4:
                    variants.add(base[:-cut])
        results: list[tuple[float, str, str, str]] = []
        for variant in variants:
            for name, kind, item_id, display in self._name_index:
                if name == variant:
                    results.append((1.0, kind, item_id, display))
            if len(variant) >= 4:
                for close_name in difflib.get_close_matches(variant, self._name_pool, n=2, cutoff=0.78):
                    score = difflib.SequenceMatcher(None, variant, close_name).ratio()
                    for name, kind, item_id, display in self._name_index:
                        if name == close_name:
                            results.append((score, kind, item_id, display))
        return results

    def auto_context(self, text: str, limit: int = 3, max_chars: int = 6000) -> tuple[str | None, list[str]]:
        """Детермінований пошук сутностей у повідомленні -> готовий блок для промпта.
        Повертає (блок або None, список знайдених імен для футера)."""
        if not self.loaded or not text:
            return None, []
        words = _WORD_RE.findall(text)
        candidates: list[str] = []
        for i, word in enumerate(words):
            if len(word) >= 3:
                candidates.append(word)
            if i + 1 < len(words):
                candidates.append(f"{word} {words[i + 1]}")

        hits: dict[tuple[str, str], tuple[float, str]] = {}
        for candidate in candidates:
            for score, kind, item_id, display in self._best_matches(candidate):
                key = (kind, item_id)
                if score > hits.get(key, (0.0, ""))[0]:
                    hits[key] = (score, display)
        for zh, kind, item_id, display in self._zh_index:
            if zh and zh in text:
                hits[(kind, item_id)] = (1.0, display)
        if not hits:
            return None, []

        priority = {"agents": 0, "wengines": 1, "discs": 2, "bangboo": 3}
        ranked = sorted(hits.items(), key=lambda kv: (-kv[1][0], priority[kv[0][0]]))

        blocks: list[str] = []
        labels: list[str] = []
        total = 0
        for (kind, item_id), (_score, display) in ranked[:limit]:
            block = self.describe(kind, item_id)
            if blocks and total + len(block) > max_chars:
                break
            blocks.append(block)
            labels.append(str(display))
            total += len(block)

        header = (
            "СПРАВОЧНЫЕ ДАННЫЕ ИЗ БАЗЫ по сущностям, упомянутым в сообщении. Они уже "
            "проверены — используй их вместо памяти; эти же записи повторно инструментами "
            "не запрашивай, недостающее добирай точечно."
        )
        return header + "\n\n" + "\n\n".join(blocks), labels

    def match_bangboo(self, team: list[str]) -> str:
        """Підбір банбу під склад команди: перевіряє умови активації проти
        профілю (елементи, фракції, спеціальності). Нерозпарсені умови
        віддаються текстом — їх оцінює модель."""
        bangboo = self._data.get("bangboo", {})
        if bangboo and not any("activation" in record for record in bangboo.values()):
            return (
                "Бази банбу згенеровані старою версією без умов активації — "
                "власнику: python -m zzz.build_db, потім /zzz_reload."
            )

        resolved: list[tuple[str, dict]] = []
        unknown: list[str] = []
        for raw_name in team:
            found = self.get("agents", str(raw_name))
            (resolved if found else unknown).append(found or str(raw_name))
        if not resolved:
            return "Не впізнав жодного агента з команди: " + ", ".join(unknown)

        traits: Counter = Counter()
        members = []
        for _, record in resolved:
            members.append(
                f"{record.get('name')} ({record.get('element')}/{record.get('specialty')}"
                f"/{record.get('faction')})"
            )
            for key in ("element", "special_element", "faction", "specialty", "attack_type"):
                value = record.get(key)
                if value:
                    traits[str(value)] += 1

        matched: list[str] = []
        manual: list[str] = []
        unconditional_s: list[str] = []
        unconditional_rest = 0
        for item_id, record in bangboo.items():
            label = f"{record.get('rarity') or '?'} {record.get('name')} (id {item_id})"
            activation = record.get("activation")
            if not activation:
                if record.get("rarity") == "S":
                    unconditional_s.append(record.get("name") or item_id)
                else:
                    unconditional_rest += 1
                continue
            subject = activation.get("subject")
            if not subject:
                manual.append(f"{label} — умова текстом: {activation.get('text', '?')}")
                continue
            needle = subject.lower()
            have = sum(
                count for trait, count in traits.items()
                if needle in trait.lower() or trait.lower() in needle
            )
            need = int(activation.get("count", 1))
            if have >= need:
                matched.append(f"{label} — умова виконана ({subject}: {have}/{need}). {activation.get('text', '')}")

        lines = [
            "Команда: " + ", ".join(members)
            + (f" · не впізнано: {', '.join(unknown)}" if unknown else ""),
            "Риси команди: " + ", ".join(f"{trait}×{count}" for trait, count in traits.most_common()),
            "",
            f"Банбу з ВИКОНАНОЮ умовою ({len(matched)}):",
            *(matched or ["— жодного"]),
        ]
        if manual:
            lines += ["", "Умова не розпарсена — оціни текстом:", *manual[:6]]
        lines += [
            "",
            f"Без умов активації: S-ранг — {', '.join(unconditional_s) or '—'};"
            f" інших {unconditional_rest} (див. zzz_overview за потреби).",
        ]
        return "\n".join(lines)

    def stats_line(self) -> str:
        counts = self.meta.get("counts", {k: len(v) for k, v in self._data.items()})
        return (
            f"ZZZ DB v{self.meta.get('game_version', '?')} ({self.meta.get('channel', '?')}), "
            f"згенеровано {self.meta.get('generated_at', '?')}: "
            + ", ".join(f"{k}={v}" for k, v in counts.items())
        )

    def _format_curated(self, curated: dict) -> str:
        """Нотатки власника + структуровані CN/West-розбіжності.
        Розбіжність зі звіркою за старіший патч отримує явне попередження."""
        current = str(self.meta.get("game_version", "") or "")
        lines: list[str] = []
        plain = {k: v for k, v in curated.items() if k != "divergences"}
        if plain:
            lines.append("[Кураторські нотатки] " + json.dumps(plain, ensure_ascii=False))
        for item in curated.get("divergences") or []:
            if not isinstance(item, dict):
                continue
            part = (
                f"[CN/West · {item.get('topic', '?')}] "
                f"CN: {item.get('cn', '—')} | West: {item.get('west', '—')}"
            )
            if item.get("reason"):
                part += f" | Причина розбіжності: {item['reason']}"
            if item.get("verdict"):
                part += f" | Вердикт власника: {item['verdict']}"
            if item.get("confidence"):
                part += f" (впевненість: {item['confidence']})"
            patch = str(item.get("patch") or "")
            if patch:
                part += f" | звірено у v{patch}"
                if current and patch != current:
                    part += f" ⚠ поточна версія даних v{current} — звірка могла застаріти"
            if item.get("note"):
                part += f" | {item['note']}"
            lines.append(part)
        return "\n".join(lines)

    # ---------------- форматери ----------------

    @staticmethod
    def _format_agents(item_id: str, r: dict) -> str:
        element = r.get("element") or "?"
        if r.get("special_element"):
            element = f"{element} ({r['special_element']})"
        lines = [
            f"АГЕНТ {r.get('name')} (id {item_id})"
            + (f" — {r['full_name']}" if r.get("full_name") else "")
            + (f" / {r['name_zh']}" if r.get("name_zh") else ""),
            f"Рідкість {r.get('rarity')} · {element} · {r.get('specialty')}"
            + (f" · {r['attack_type']}" if r.get("attack_type") else "")
            + (f" · фракція: {r['faction']}" if r.get("faction") else ""),
        ]
        if r.get("strategy"):
            lines.append("Плейстайл: " + " | ".join(r["strategy"]))
        if r.get("base_stats"):
            stats = ", ".join(
                f"{k}={v:g}"
                for k, v in list(r["base_stats"].items())[:12]
                if isinstance(v, (int, float))
            )
            lines.append(f"Базові статки: {stats}")
        core = r.get("core_skill") or {}
        if core.get("brief"):
            lines.append(f"Core: {core.get('name') or ''} — {core['brief']}")
        for skill_type, moves in (r.get("skills") or {}).items():
            if isinstance(moves, dict):  # сумісність зі старим форматом (один рух)
                moves = [moves]
            for move in moves:
                lines.append(f"{skill_type}: {move.get('name')} — {move.get('brief')}")
        recommend = r.get("game_recommend")
        if recommend:
            parts = []
            for name_key, id_key, label in (
                ("disc_4pc", "disc_4pc_id", "4pc"),
                ("disc_2pc", "disc_2pc_id", "2pc"),
                ("disc_alt", "disc_alt_id", "альт. 2pc"),
            ):
                value = recommend.get(name_key) or recommend.get(id_key)
                if value:
                    parts.append(f"{label} {value}")
            stats_txt = ", ".join(f"{k}: {v}" for k, v in (recommend.get("main_stats") or {}).items())
            line = "Гра рекомендує: " + " + ".join(parts) if parts else "Гра рекомендує"
            if stats_txt:
                line += f" · головні стати: {stats_txt}"
            lines.append(line)
        for level, cinema in (r.get("mindscapes") or {}).items():
            lines.append(f"M{level}: {cinema.get('name')} — {cinema.get('brief')}")
        return "\n".join(lines)

    @staticmethod
    def _format_wengines(item_id: str, r: dict) -> str:
        base = r.get("base_stat") or {}
        adv = r.get("adv_stat") or {}
        lines = [
            f"W-ENGINE {r.get('name')} (id {item_id})"
            + (f" / {r['name_zh']}" if r.get("name_zh") else ""),
            f"Рідкість {r.get('rarity')} · спеціальність: {r.get('specialty')}",
            f"База: {base.get('stat')}={base.get('value')} · Просунута: {adv.get('stat')}={adv.get('value')}",
        ]
        if r.get("effect_r1"):
            lines.append(f"Ефект R1 ({r.get('effect_name')}): {r['effect_r1']}")
        if r.get("effect_r5") and r.get("effect_r5") != r.get("effect_r1"):
            lines.append(f"Ефект R5: {r['effect_r5']}")
        return "\n".join(lines)

    @staticmethod
    def _format_discs(item_id: str, r: dict) -> str:
        return "\n".join(
            [
                f"DRIVE DISC {r.get('name')} (id {item_id})"
                + (f" / {r['name_zh']}" if r.get("name_zh") else ""),
                f"2pc: {r.get('set2')}",
                f"4pc: {r.get('set4')}",
            ]
        )

    @staticmethod
    def _format_bangboo(item_id: str, r: dict) -> str:
        lines = [
            f"БАНБУ {r.get('name')} (id {item_id})"
            + (f" / {r['name_zh']}" if r.get("name_zh") else ""),
            f"Рідкість {r.get('rarity')}" + (f" · {r['description']}" if r.get("description") else ""),
        ]
        for slot, skill in (r.get("skills") or {}).items():
            lines.append(f"Навичка {slot}: {skill.get('name')} — {skill.get('brief')}")
        return "\n".join(lines)
