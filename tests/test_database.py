"""Тести Database (aiosqlite): усі таблиці + delete_last_assistant + channel_modes.

Кожен тест бере власну БД у пам'яті/тимчасовому файлі, тож ізольований.
"""
from __future__ import annotations

import pytest

from database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.init()
    yield database
    await database.close()


# ---------------- chat_history ----------------

async def test_add_and_get_chat_history_order(db):
    await db.add_chat_message(1, 10, "user", "перше")
    await db.add_chat_message(1, 10, "assistant", "друге")
    await db.add_chat_message(1, 10, "user", "третє")
    rows = await db.get_chat_history(1)
    assert [r["content"] for r in rows] == ["перше", "друге", "третє"]
    assert [r["role"] for r in rows] == ["user", "assistant", "user"]


async def test_chat_history_is_per_channel(db):
    await db.add_chat_message(1, None, "user", "канал 1")
    await db.add_chat_message(2, None, "user", "канал 2")
    rows1 = await db.get_chat_history(1)
    rows2 = await db.get_chat_history(2)
    assert [r["content"] for r in rows1] == ["канал 1"]
    assert [r["content"] for r in rows2] == ["канал 2"]


async def test_get_chat_history_limit(db):
    for i in range(10):
        await db.add_chat_message(1, None, "user", f"msg{i}")
    rows = await db.get_chat_history(1, limit=3)
    # останні 3 у хронологічному порядку
    assert [r["content"] for r in rows] == ["msg7", "msg8", "msg9"]


async def test_clear_chat_history(db):
    await db.add_chat_message(1, None, "user", "a")
    await db.add_chat_message(1, None, "user", "b")
    deleted = await db.clear_chat_history(1)
    assert deleted == 2
    assert await db.get_chat_history(1) == []


async def test_delete_last_assistant(db):
    await db.add_chat_message(1, None, "user", "u1")
    await db.add_chat_message(1, None, "assistant", "a1")
    await db.add_chat_message(1, None, "user", "u2")
    await db.add_chat_message(1, None, "assistant", "a2")
    ok = await db.delete_last_assistant(1)
    assert ok is True
    rows = await db.get_chat_history(1)
    assert [r["content"] for r in rows] == ["u1", "a1", "u2"]


async def test_delete_last_assistant_none_present(db):
    await db.add_chat_message(1, None, "user", "u1")
    assert await db.delete_last_assistant(1) is False


async def test_delete_last_assistant_scoped_to_channel(db):
    await db.add_chat_message(1, None, "assistant", "keep")
    await db.add_chat_message(2, None, "assistant", "drop")
    await db.delete_last_assistant(2)
    assert [r["content"] for r in await db.get_chat_history(1)] == ["keep"]
    assert await db.get_chat_history(2) == []


# ---------------- reminders ----------------

async def test_add_and_due_reminders(db):
    rid = await db.add_reminder(user_id=5, channel_id=1, guild_id=10, text="пити воду", remind_at=100)
    assert isinstance(rid, int) and rid > 0
    due = await db.due_reminders(now=150)
    assert len(due) == 1
    assert due[0]["text"] == "пити воду"
    assert due[0]["id"] == rid


async def test_due_reminders_excludes_future(db):
    await db.add_reminder(5, 1, 10, "пізніше", remind_at=1000)
    assert await db.due_reminders(now=100) == []


async def test_complete_reminder_marks_done(db):
    rid = await db.add_reminder(5, 1, 10, "x", remind_at=100)
    await db.complete_reminder(rid)
    assert await db.due_reminders(now=200) == []


async def test_user_reminders_lists_pending(db):
    await db.add_reminder(5, 1, 10, "a", remind_at=100)
    await db.add_reminder(5, 1, 10, "b", remind_at=50)
    rows = await db.user_reminders(5)
    # відсортовано за remind_at
    assert [r["text"] for r in rows] == ["b", "a"]


async def test_user_reminders_excludes_done(db):
    rid = await db.add_reminder(5, 1, 10, "done", remind_at=100)
    await db.add_reminder(5, 1, 10, "pending", remind_at=100)
    await db.complete_reminder(rid)
    rows = await db.user_reminders(5)
    assert [r["text"] for r in rows] == ["pending"]


async def test_delete_reminder_owner_check(db):
    rid = await db.add_reminder(5, 1, 10, "x", remind_at=100)
    assert await db.delete_reminder(rid, user_id=999) is False  # чужий
    assert await db.delete_reminder(rid, user_id=5) is True     # власник
    assert await db.delete_reminder(rid, user_id=5) is False    # вже нема


# ---------------- warns ----------------

async def test_add_warn_returns_count(db):
    assert await db.add_warn(1, 5, 100, "спам") == 1
    assert await db.add_warn(1, 5, 100, "флуд") == 2


async def test_get_warns(db):
    await db.add_warn(1, 5, 100, "перше")
    await db.add_warn(1, 5, 100, "друге")
    warns = await db.get_warns(1, 5)
    assert [w["reason"] for w in warns] == ["перше", "друге"]
    assert all(w["moderator_id"] == 100 for w in warns)


async def test_warns_scoped_by_guild_and_user(db):
    await db.add_warn(1, 5, 100, "a")
    await db.add_warn(2, 5, 100, "b")  # інший сервер
    await db.add_warn(1, 6, 100, "c")  # інший юзер
    assert len(await db.get_warns(1, 5)) == 1


async def test_clear_warns(db):
    await db.add_warn(1, 5, 100, "a")
    await db.add_warn(1, 5, 100, "b")
    assert await db.clear_warns(1, 5) == 2
    assert await db.get_warns(1, 5) == []


# ---------------- levels (XP) ----------------

async def test_add_xp_accumulates(db):
    assert await db.add_xp(1, 5, 10) == 10
    assert await db.add_xp(1, 5, 15) == 25


async def test_get_xp_default_zero(db):
    assert await db.get_xp(1, 999) == 0


async def test_top_xp_ordering(db):
    await db.add_xp(1, 5, 100)
    await db.add_xp(1, 6, 300)
    await db.add_xp(1, 7, 200)
    top = await db.top_xp(1, limit=10)
    assert [r["user_id"] for r in top] == [6, 7, 5]


async def test_top_xp_respects_limit(db):
    for uid in range(5):
        await db.add_xp(1, uid, uid * 10)
    assert len(await db.top_xp(1, limit=2)) == 2


async def test_xp_scoped_by_guild(db):
    await db.add_xp(1, 5, 100)
    await db.add_xp(2, 5, 50)
    assert await db.get_xp(1, 5) == 100
    assert await db.get_xp(2, 5) == 50


# ---------------- channel_modes ----------------

async def test_channel_mode_default_none(db):
    assert await db.get_channel_mode(1) is None


async def test_set_and_get_channel_mode(db):
    await db.set_channel_mode(1, "zzz")
    assert await db.get_channel_mode(1) == "zzz"


async def test_set_channel_mode_normal_removes(db):
    await db.set_channel_mode(1, "zzz")
    await db.set_channel_mode(1, "normal")
    assert await db.get_channel_mode(1) is None


async def test_set_channel_mode_none_removes(db):
    await db.set_channel_mode(1, "zzz")
    await db.set_channel_mode(1, None)
    assert await db.get_channel_mode(1) is None


async def test_set_channel_mode_overwrites(db):
    await db.set_channel_mode(1, "zzz")
    await db.set_channel_mode(1, "other")
    assert await db.get_channel_mode(1) == "other"


# ---------------- usage_log (облік витрат токенів) ----------------

async def test_usage_summary_empty(db):
    # порожній лог: усі суми нульові, кількість відповідей 0
    summary = await db.usage_summary(days=1, now=1000)
    assert summary["replies"] == 0
    assert summary["prompt_tokens"] == 0
    assert summary["completion_tokens"] == 0
    assert summary["total_tokens"] == 0


async def test_log_and_summarize_single(db):
    await db.log_usage(channel_id=1, guild_id=10, prompt_tokens=100, completion_tokens=20, mode="normal", now=1000)
    summary = await db.usage_summary(days=1, now=1000)
    assert summary["replies"] == 1
    assert summary["prompt_tokens"] == 100
    assert summary["completion_tokens"] == 20
    assert summary["total_tokens"] == 120


async def test_usage_summary_sums_multiple(db):
    await db.log_usage(1, 10, 100, 20, "normal", now=1000)
    await db.log_usage(1, 10, 50, 10, "zzz", now=1001)
    await db.log_usage(2, 10, 30, 5, "normal", now=1002)
    summary = await db.usage_summary(days=1, now=1002)
    assert summary["replies"] == 3
    assert summary["prompt_tokens"] == 180
    assert summary["completion_tokens"] == 35
    assert summary["total_tokens"] == 215


async def test_usage_summary_window_excludes_old(db):
    # запис старший за вікно (days=1 = 86400 с) не потрапляє в підсумок
    await db.log_usage(1, 10, 100, 20, "normal", now=1000)          # старий
    await db.log_usage(1, 10, 7, 3, "normal", now=1000 + 86400)     # свіжий
    summary = await db.usage_summary(days=1, now=1000 + 86400)
    assert summary["replies"] == 1
    assert summary["prompt_tokens"] == 7
    assert summary["completion_tokens"] == 3


async def test_usage_summary_seven_day_window_wider(db):
    await db.log_usage(1, 10, 100, 20, "normal", now=1_000_000)
    # 3 доби тому — поза добовим вікном, але всередині тижневого
    await db.log_usage(1, 10, 40, 8, "normal", now=1_000_000 - 3 * 86400)
    day = await db.usage_summary(days=1, now=1_000_000)
    week = await db.usage_summary(days=7, now=1_000_000)
    assert day["replies"] == 1 and day["prompt_tokens"] == 100
    assert week["replies"] == 2 and week["prompt_tokens"] == 140


async def test_usage_summary_top_mode(db):
    await db.log_usage(1, 10, 10, 1, "zzz", now=1000)
    await db.log_usage(1, 10, 10, 1, "zzz", now=1001)
    await db.log_usage(1, 10, 10, 1, "normal", now=1002)
    summary = await db.usage_summary(days=1, now=1002)
    # найчастіший режим — zzz (2 з 3)
    assert summary["top_mode"] == "zzz"


async def test_log_usage_defaults_now_to_time(db):
    # без явного now пише поточний час — запис одразу в добовому вікні
    await db.log_usage(1, 10, 5, 5, "normal")
    summary = await db.usage_summary(days=1)
    assert summary["replies"] == 1
