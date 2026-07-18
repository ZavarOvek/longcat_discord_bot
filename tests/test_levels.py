"""Тести cogs.levels:
- чисті формули xp_needed / level_from_xp;
- прибирання _cooldowns: протухлі ключі викидаються, свіжі лишаються,
  словник не росте безмежно.
"""
from __future__ import annotations

from types import SimpleNamespace

from cogs.levels import (
    COOLDOWN_SWEEP_EVERY,
    LevelsCog,
    XP_COOLDOWN_SECONDS,
    level_from_xp,
    xp_needed,
)


# ---------------- формули ----------------

def test_xp_needed_formula():
    # 5n² + 50n + 100
    assert xp_needed(0) == 100
    assert xp_needed(1) == 155
    assert xp_needed(2) == 220


def test_level_from_xp_zero():
    level, current, needed = level_from_xp(0)
    assert level == 0
    assert current == 0
    assert needed == xp_needed(0)


def test_level_from_xp_accumulates():
    # рівно на межу 1-го рівня
    total = xp_needed(0)
    level, current, needed = level_from_xp(total)
    assert level == 1
    assert current == 0
    assert needed == xp_needed(1)


def test_level_from_xp_partial_progress():
    total = xp_needed(0) + 30
    level, current, needed = level_from_xp(total)
    assert level == 1
    assert current == 30


# ---------------- прибирання кулдаунів ----------------

def _cog():
    bot = SimpleNamespace(db=None)
    return LevelsCog(bot)


def test_touch_records_cooldown():
    cog = _cog()
    cog._touch_cooldown((1, 2), now=1000.0)
    assert cog._cooldowns[(1, 2)] == 1000.0


def test_sweep_drops_stale_keeps_fresh():
    cog = _cog()
    now = 10_000.0
    # старий ключ (протух), свіжий ключ
    cog._cooldowns[(1, 1)] = now - XP_COOLDOWN_SECONDS - 1
    cog._cooldowns[(1, 2)] = now - 1
    cog._sweep_cooldowns(now)
    assert (1, 1) not in cog._cooldowns
    assert (1, 2) in cog._cooldowns


def test_touch_triggers_sweep_and_bounds_growth():
    cog = _cog()
    base = 100_000.0
    # багато протухлих ключів, вставлених повз лічильник (age > cooldown при base)
    for i in range(COOLDOWN_SWEEP_EVERY):
        cog._cooldowns[(0, i)] = base - XP_COOLDOWN_SECONDS - 100
    # COOLDOWN_SWEEP_EVERY дотиків при часі base -> останній тригерить sweep,
    # який побачить, що всі (0, i) вже протухли, і викине їх
    for i in range(COOLDOWN_SWEEP_EVERY):
        cog._touch_cooldown((1, i), now=base)
    # протухлі прибрані; лишились лише свіжі (1, i), вставлені при base
    assert not any(key[0] == 0 for key in cog._cooldowns)
    assert (1, COOLDOWN_SWEEP_EVERY - 1) in cog._cooldowns
