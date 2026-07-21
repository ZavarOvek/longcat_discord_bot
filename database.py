"""Асинхронне SQLite-сховище бота (aiosqlite).

Таблиці:
- chat_history — пам'ять LLM-розмов, окремо на кожен канал/гілку/DM
- reminders    — нагадування (переживають рестарт)
- warns        — попередження модерації
- levels       — XP за активність
"""
from __future__ import annotations

import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL,
    guild_id INTEGER,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_channel ON chat_history(channel_id, id);

CREATE TABLE IF NOT EXISTS reminders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    guild_id INTEGER,
    text TEXT NOT NULL,
    remind_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(done, remind_at);

CREATE TABLE IF NOT EXISTS warns(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warns_user ON warns(guild_id, user_id);

CREATE TABLE IF NOT EXISTS levels(
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS channel_modes(
    channel_id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL,
    guild_id INTEGER,
    ts INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    mode TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ---------------- Історія чату ----------------

    async def add_chat_message(self, channel_id: int, guild_id: int | None, role: str, content: str) -> None:
        await self._db.execute(
            "INSERT INTO chat_history(channel_id, guild_id, role, content, created_at) VALUES(?,?,?,?,?)",
            (channel_id, guild_id, role, content, int(time.time())),
        )
        await self._db.commit()

    async def get_chat_history(self, channel_id: int, limit: int = 300) -> list[aiosqlite.Row]:
        """Останні `limit` повідомлень каналу в хронологічному порядку."""
        cursor = await self._db.execute(
            "SELECT role, content FROM chat_history WHERE channel_id=? ORDER BY id DESC LIMIT ?",
            (channel_id, limit),
        )
        rows = await cursor.fetchall()
        return list(reversed(rows))

    async def clear_chat_history(self, channel_id: int) -> int:
        cursor = await self._db.execute("DELETE FROM chat_history WHERE channel_id=?", (channel_id,))
        await self._db.commit()
        return cursor.rowcount

    # ---------------- Нагадування ----------------

    async def add_reminder(
        self, user_id: int, channel_id: int, guild_id: int | None, text: str, remind_at: int
    ) -> int:
        cursor = await self._db.execute(
            "INSERT INTO reminders(user_id, channel_id, guild_id, text, remind_at, created_at) VALUES(?,?,?,?,?,?)",
            (user_id, channel_id, guild_id, text, remind_at, int(time.time())),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def due_reminders(self, now: int) -> list[aiosqlite.Row]:
        cursor = await self._db.execute(
            "SELECT id, user_id, channel_id, text FROM reminders WHERE done=0 AND remind_at<=? ORDER BY remind_at LIMIT 25",
            (now,),
        )
        return await cursor.fetchall()

    async def complete_reminder(self, reminder_id: int) -> None:
        await self._db.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
        await self._db.commit()

    async def user_reminders(self, user_id: int) -> list[aiosqlite.Row]:
        cursor = await self._db.execute(
            "SELECT id, text, remind_at FROM reminders WHERE done=0 AND user_id=? ORDER BY remind_at",
            (user_id,),
        )
        return await cursor.fetchall()

    async def delete_reminder(self, reminder_id: int, user_id: int) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM reminders WHERE id=? AND user_id=? AND done=0",
            (reminder_id, user_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # ---------------- Попередження ----------------

    async def add_warn(self, guild_id: int, user_id: int, moderator_id: int, reason: str | None) -> int:
        """Додає попередження і повертає їх нову кількість у користувача."""
        await self._db.execute(
            "INSERT INTO warns(guild_id, user_id, moderator_id, reason, created_at) VALUES(?,?,?,?,?)",
            (guild_id, user_id, moderator_id, reason, int(time.time())),
        )
        await self._db.commit()
        cursor = await self._db.execute(
            "SELECT COUNT(*) AS n FROM warns WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        row = await cursor.fetchone()
        return row["n"]

    async def get_warns(self, guild_id: int, user_id: int) -> list[aiosqlite.Row]:
        cursor = await self._db.execute(
            "SELECT id, moderator_id, reason, created_at FROM warns WHERE guild_id=? AND user_id=? ORDER BY id",
            (guild_id, user_id),
        )
        return await cursor.fetchall()

    async def clear_warns(self, guild_id: int, user_id: int) -> int:
        cursor = await self._db.execute(
            "DELETE FROM warns WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        await self._db.commit()
        return cursor.rowcount

    # ---------------- Рівні (XP) ----------------

    async def add_xp(self, guild_id: int, user_id: int, amount: int) -> int:
        """Додає XP і повертає новий сумарний XP."""
        await self._db.execute(
            "INSERT INTO levels(guild_id, user_id, xp) VALUES(?,?,?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = xp + excluded.xp",
            (guild_id, user_id, amount),
        )
        await self._db.commit()
        return await self.get_xp(guild_id, user_id)

    async def get_xp(self, guild_id: int, user_id: int) -> int:
        cursor = await self._db.execute(
            "SELECT xp FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        row = await cursor.fetchone()
        return row["xp"] if row else 0

    async def top_xp(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        cursor = await self._db.execute(
            "SELECT user_id, xp FROM levels WHERE guild_id=? ORDER BY xp DESC LIMIT ?",
            (guild_id, limit),
        )
        return await cursor.fetchall()

    # ---------------- Режими каналів ----------------

    async def get_channel_mode(self, channel_id: int) -> str | None:
        cursor = await self._db.execute(
            "SELECT mode FROM channel_modes WHERE channel_id=?", (channel_id,)
        )
        row = await cursor.fetchone()
        return row["mode"] if row else None

    async def set_channel_mode(self, channel_id: int, mode: str | None) -> None:
        """mode=None або 'normal' — прибрати запис (звичайний чат)."""
        if mode in (None, "normal"):
            await self._db.execute("DELETE FROM channel_modes WHERE channel_id=?", (channel_id,))
        else:
            await self._db.execute(
                "INSERT INTO channel_modes(channel_id, mode) VALUES(?,?) "
                "ON CONFLICT(channel_id) DO UPDATE SET mode=excluded.mode",
                (channel_id, mode),
            )
        await self._db.commit()

    # ---------------- Облік витрат токенів ----------------

    async def log_usage(
        self,
        channel_id: int,
        guild_id: int | None,
        prompt_tokens: int,
        completion_tokens: int,
        mode: str,
        now: int | None = None,
    ) -> None:
        """Один запис на відповідь користувачу (сума токенів усіх LLM-викликів)."""
        ts = int(time.time()) if now is None else now
        await self._db.execute(
            "INSERT INTO usage_log(channel_id, guild_id, ts, prompt_tokens, completion_tokens, mode) "
            "VALUES(?,?,?,?,?,?)",
            (channel_id, guild_id, ts, prompt_tokens, completion_tokens, mode),
        )
        await self._db.commit()

    async def usage_summary(self, days: int = 1, now: int | None = None) -> dict:
        """Підсумок витрат за останні `days` діб: кількість відповідей, токени
        prompt/completion/total і найчастіший режим (top_mode, None якщо порожньо)."""
        now = int(time.time()) if now is None else now
        since = now - days * 86400
        cursor = await self._db.execute(
            "SELECT COUNT(*) AS replies, "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens "
            "FROM usage_log WHERE ts > ?",
            (since,),
        )
        row = await cursor.fetchone()
        prompt_tokens = row["prompt_tokens"]
        completion_tokens = row["completion_tokens"]
        mode_cursor = await self._db.execute(
            "SELECT mode, COUNT(*) AS n FROM usage_log WHERE ts > ? "
            "GROUP BY mode ORDER BY n DESC LIMIT 1",
            (since,),
        )
        mode_row = await mode_cursor.fetchone()
        return {
            "replies": row["replies"],
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "top_mode": mode_row["mode"] if mode_row else None,
        }

    async def delete_last_assistant(self, channel_id: int) -> bool:
        """Видаляє останню відповідь бота в каналі (для 🔁 переролу)."""
        cursor = await self._db.execute(
            "DELETE FROM chat_history WHERE id = ("
            "SELECT id FROM chat_history WHERE channel_id=? AND role='assistant' "
            "ORDER BY id DESC LIMIT 1)",
            (channel_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0
