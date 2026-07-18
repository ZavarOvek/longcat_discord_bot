"""Спільні фікстури й ізоляція від зовнішніх залежностей.

`zzz.build_db` імпортує `hakushin` на рівні модуля, а `httpx`/`ddgs` смикають
мережу. Тести перевіряють чисту логіку, тож важкі/мережеві залежності або
застаблені (hakushin), або мокаються в конкретних тестах (httpx, ddgs).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_hakushin() -> None:
    """Легкий стаб пакета hakushin — щоб `import zzz.build_db` не вимагав
    реального (важкого, мережевого) пакета. Тести build_db перевіряють лише
    чисті трансформації, які hakushin у рантаймі не викликають."""
    if "hakushin" in sys.modules:
        return

    hakushin = types.ModuleType("hakushin")

    class _Enum:
        ZZZ = "zzz"

        def __init__(self, value="en"):
            self.value = value

    hakushin.Game = _Enum
    hakushin.Language = _Enum
    hakushin.HakushinAPI = object  # у тестах не інстанціюється

    models = types.ModuleType("hakushin.models")
    zzz_mod = types.ModuleType("hakushin.models.zzz")
    zzz_mod.CharacterDetail = object
    models.zzz = zzz_mod

    sys.modules["hakushin"] = hakushin
    sys.modules["hakushin.models"] = models
    sys.modules["hakushin.models.zzz"] = zzz_mod


_stub_hakushin()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
