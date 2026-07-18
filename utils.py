"""Спільні допоміжні функції: розбиття повідомлень, парсинг часу та кубиків."""
from __future__ import annotations

import random
import re

DISCORD_LIMIT = 2000
SAFE_LIMIT = 1900  # запас на закриття/відкриття код-блоків

_FENCE_RE = re.compile(r"```(\w*)")


def _scan_fences(chunk: str) -> str | None:
    """Повертає мову відкритого код-блоку в кінці шматка, або None якщо всі закриті."""
    opened: str | None = None
    for match in _FENCE_RE.finditer(chunk):
        if opened is None:
            opened = match.group(1) or ""
        else:
            opened = None
    return opened


def split_message(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """Розбиває текст на шматки <= limit, намагаючись різати по абзацах,
    і акуратно закриває/перевідкриває код-блоки, розрізані навпіл."""
    text = (text or "").strip()
    if not text:
        return ["…"]

    chunks: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 3:
            cut = window.rfind("\n")
        if cut < limit // 3:
            cut = window.rfind(" ")
        if cut < limit // 3:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    chunks.append(text)

    fixed: list[str] = []
    open_lang: str | None = None
    for chunk in chunks:
        if open_lang is not None:
            chunk = f"```{open_lang}\n{chunk}"
        open_lang = _scan_fences(chunk)
        if open_lang is not None:
            chunk += "\n```"
        fixed.append(chunk)
    return fixed


# --- тривалості: "10m", "2h", "1d", "1h30m", "2д", "10хв", голе число = хвилини ---
_DUR_RE = re.compile(r"(\d+)\s*(дн|д|год|г|хв|сек|с|d|h|m|s)", re.IGNORECASE)
_UNIT_SECONDS = {
    "д": 86400, "дн": 86400, "d": 86400,
    "г": 3600, "год": 3600, "h": 3600,
    "хв": 60, "m": 60,
    "с": 1, "сек": 1, "s": 1,
}


def parse_duration(raw: str) -> int | None:
    """Секунди з рядка тривалості. None, якщо розпарсити не вдалося."""
    raw = (raw or "").strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw) * 60  # голе число трактуємо як хвилини
    total = 0
    for number, unit in _DUR_RE.findall(raw):
        total += int(number) * _UNIT_SECONDS[unit.lower()]
    return total or None


# --- markdown-таблиці: Discord їх не рендерить, конвертуємо в рядки ---
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _is_table_separator(line: str) -> bool:
    """Рядок-роздільник виду |---|:---:|."""
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= set("|-: \t") and stripped.count("-") >= 2


def _render_table(block: list[str]) -> list[str]:
    """Блок |-рядків -> «— **перша клітинка**: решта · через · крапку»."""
    has_separator = any(_is_table_separator(line) for line in block)
    rows: list[list[str]] = []
    for line in block:
        if _is_table_separator(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        while cells and cells[-1] == "":
            cells.pop()
        if cells:
            rows.append(cells)
    if not has_separator or len(rows) < 2:
        return block  # не схоже на markdown-таблицю — не чіпаємо
    rendered: list[str] = []
    for cells in rows[1:]:  # перший рядок — заголовок; він очевидний з контенту
        first = cells[0].strip("*").strip() if cells else ""
        rest = [cell for cell in cells[1:] if cell]
        if first and rest:
            rendered.append(f"— **{first}**: " + " · ".join(rest))
        elif first:
            rendered.append(f"— {first}")
        elif rest:
            rendered.append("— " + " · ".join(rest))
    return rendered


def fix_tables(text: str) -> str:
    """Замінює markdown-таблиці (| … |) на читабельні рядки — Discord таблиці
    не рендерить ніде. Вміст код-блоків не чіпається."""
    lines = (text or "").split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence or not _TABLE_ROW_RE.match(line):
            out.append(line)
            i += 1
            continue
        block: list[str] = []
        while (
            i < len(lines)
            and not lines[i].lstrip().startswith("```")
            and _TABLE_ROW_RE.match(lines[i])
        ):
            block.append(lines[i])
            i += 1
        out.extend(_render_table(block))
    return "\n".join(out)


# --- мовний вартовий: детект повністю української відповіді ---
_UKR_ONLY_CHARS = set("іїєґІЇЄҐ")


def looks_ukrainian(text: str, threshold: int = 6) -> bool:
    """Груба, але надійна евристика: літери і/ї/є/ґ не існують у російському
    тексті, тож їх кількість >= threshold означає українську відповідь
    (поодинокі вкраплення типу «нема питань» цих літер не містять)."""
    return sum(1 for ch in (text or "") if ch in _UKR_ONLY_CHARS) >= threshold


# --- кубики: NdM+K, наприклад 2d6+3 або 1д20 ---
_DICE_RE = re.compile(r"^\s*(\d{0,3})\s*[dд]\s*(\d{1,4})\s*([+-]\s*\d{1,4})?\s*$", re.IGNORECASE)


def roll_dice(formula: str) -> tuple[list[int], int, int]:
    """Повертає (кидки, модифікатор, сума). ValueError при неправильній формулі."""
    match = _DICE_RE.match(formula or "")
    if not match:
        raise ValueError("Формат: NdM(+K), наприклад 2d6+3 або 1d20")
    count = int(match.group(1) or 1)
    faces = int(match.group(2))
    modifier = int((match.group(3) or "0").replace(" ", ""))
    if not 1 <= count <= 100:
        raise ValueError("Кількість кубиків: від 1 до 100")
    if not 2 <= faces <= 1000:
        raise ValueError("Кількість граней: від 2 до 1000")
    rolls = [random.randint(1, faces) for _ in range(count)]
    return rolls, modifier, sum(rolls) + modifier
