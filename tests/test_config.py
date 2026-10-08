"""Тести config: розбір булевого LANG_GUARD.

Старе значення `LANG_GUARD=ru` має падати гучно, а не вимикати вартового
мовчки: тихий фолбек тут означає тижні відповідей не тією мовою без жодного
сліду в логах.

load_config читає .env, тож обов'язкові ключі й PERSONA_FILE фікстура
підміняє через monkeypatch — інакше результат залежав би від машини.
"""

from __future__ import annotations

import pytest

from config import load_config


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Детермінований мінімум оточення; повертає сетер решти змінних."""
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("LONGCAT_API_KEY", "test-key")
    # файлу персони немає -> вбудовані тексти, незалежно від машини
    monkeypatch.setenv("PERSONA_FILE", str(tmp_path / "немає.toml"))
    return monkeypatch.setenv


def test_lang_guard_true_enables(env):
    env("LANG_GUARD", "true")
    assert load_config().lang_guard is True


def test_lang_guard_false_disables(env):
    env("LANG_GUARD", "false")
    assert load_config().lang_guard is False


def test_lang_guard_empty_disables(env):
    env("LANG_GUARD", "")
    assert load_config().lang_guard is False


def test_lang_guard_legacy_ru_exits(env, capsys):
    env("LANG_GUARD", "ru")
    with pytest.raises(SystemExit) as exc:
        load_config()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "LANG_GUARD" in err
    assert "«ru»" in err
