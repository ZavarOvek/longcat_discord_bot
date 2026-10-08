"""Хто може стирати пам'ять і перемикати режими.

- /reset і кнопка 🧹: у ЛС — будь-хто (розмова лише з ним), на сервері —
  тільки адміністратор;
- /mode і /zzz_reload: лише на сервері й лише адміністратор.

Discord-обʼєкти — SimpleNamespace-фейки; мережі немає.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from cogs.chat import MEMORY_ADMIN_ONLY, ChatCog, ReplyView, can_manage_memory
from cogs.zzz import ZZZCog
from persona import DEFAULT_PERSONA


class FakeDB:
    def __init__(self):
        self.messages = [{"role": "user", "content": "старе"}]

    async def clear_chat_history(self, channel_id):
        n = len(self.messages)
        self.messages.clear()
        return n

    async def add_chat_message(self, channel_id, guild_id, role, content):
        self.messages.append({"role": role, "content": content})


class FakeResponse:
    def __init__(self):
        self.sent: list[tuple[str, bool]] = []

    async def send_message(self, content, *, ephemeral=False):
        self.sent.append((content, ephemeral))


GUILD = SimpleNamespace(id=7, name="Test Guild")


def _member(*, admin: bool):
    return SimpleNamespace(id=1, guild_permissions=discord.Permissions(administrator=admin))


def _interaction(user, guild):
    return SimpleNamespace(
        user=user,
        guild=guild,
        guild_id=guild.id if guild else None,
        channel_id=42,
        response=FakeResponse(),
    )


def _cog():
    db = FakeDB()
    config = SimpleNamespace(persona=DEFAULT_PERSONA)
    return ChatCog(SimpleNamespace(db=db, config=config)), db


# ---------------- can_manage_memory ----------------


def test_dm_user_can_manage_own_memory():
    assert can_manage_memory(SimpleNamespace(id=1), None)


def test_guild_admin_can_manage_memory():
    assert can_manage_memory(_member(admin=True), GUILD)


def test_guild_member_without_admin_cannot():
    assert not can_manage_memory(_member(admin=False), GUILD)


def test_user_without_guild_permissions_in_guild_cannot():
    # discord.User (не Member) на сервері — прав не видно, тож відмова.
    assert not can_manage_memory(SimpleNamespace(id=1), GUILD)


# ---------------- /reset ----------------


@pytest.mark.asyncio
async def test_reset_rejects_non_admin_in_guild():
    cog, db = _cog()
    interaction = _interaction(_member(admin=False), GUILD)
    await ChatCog.reset.callback(cog, interaction)
    assert interaction.response.sent == [(MEMORY_ADMIN_ONLY, True)]
    assert db.messages == [{"role": "user", "content": "старе"}]  # пам'ять ціла


@pytest.mark.asyncio
async def test_reset_allowed_for_admin_in_guild():
    cog, db = _cog()
    interaction = _interaction(_member(admin=True), GUILD)
    await ChatCog.reset.callback(cog, interaction)
    assert db.messages == [{"role": "user", "content": DEFAULT_PERSONA.reset_marker}]


@pytest.mark.asyncio
async def test_reset_allowed_in_dm():
    cog, db = _cog()
    interaction = _interaction(SimpleNamespace(id=1), None)
    await ChatCog.reset.callback(cog, interaction)
    assert db.messages == [{"role": "user", "content": DEFAULT_PERSONA.reset_marker}]


# ---------------- кнопка 🧹 ----------------


@pytest.mark.asyncio
async def test_forget_button_rejects_non_admin_in_guild():
    cog, db = _cog()
    view = ReplyView(cog, user_message=SimpleNamespace(author=SimpleNamespace(id=1)))
    interaction = _interaction(_member(admin=False), GUILD)
    await view.forget_button.callback(interaction)
    assert interaction.response.sent == [(MEMORY_ADMIN_ONLY, True)]
    assert db.messages == [{"role": "user", "content": "старе"}]


@pytest.mark.asyncio
async def test_forget_button_allowed_for_admin():
    cog, db = _cog()
    view = ReplyView(cog, user_message=SimpleNamespace(author=SimpleNamespace(id=1)))
    interaction = _interaction(_member(admin=True), GUILD)
    await view.forget_button.callback(interaction)
    assert db.messages == [{"role": "user", "content": DEFAULT_PERSONA.reset_marker}]


# ---------------- /mode і /zzz_reload ----------------


@pytest.mark.parametrize("command", [ZZZCog.mode, ZZZCog.zzz_reload], ids=["mode", "zzz_reload"])
def test_zzz_commands_are_admin_only_and_guild_only(command):
    assert command.default_permissions is not None
    assert command.default_permissions.administrator
    assert command.guild_only
    assert command.checks, "очікується перевірка has_permissions(administrator=True)"


@pytest.mark.parametrize("command", [ZZZCog.mode, ZZZCog.zzz_reload], ids=["mode", "zzz_reload"])
def test_zzz_command_check_rejects_non_admin(command):
    (check,) = command.checks
    non_admin = SimpleNamespace(permissions=discord.Permissions.none())
    with pytest.raises(app_commands.MissingPermissions):
        check(non_admin)
    admin = SimpleNamespace(permissions=discord.Permissions(administrator=True))
    assert check(admin)
