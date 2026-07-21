**English** | [Українська](README.uk.md)

# 🐈‍⬛ LongCat Discord Bot

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE) [![Tests](https://img.shields.io/badge/tests-213%20passing-brightgreen.svg)](CHANGELOG.md)

A personal Discord bot in Python: an LLM chat built on **Meituan
LongCat-2.0** (OpenAI-compatible API) with tools and conversation memory,
plus a classic set of server features (moderation, reminders, polls,
levels). Runs locally, keeps everything in a SQLite file next to itself.

## Features

**LLM chat with LongCat.** The bot replies to a mention, a reply to its own
message, or any message in DMs. Conversation memory is kept separately per
channel and thread and survives restarts. Tools available to the model: the
current time, server/user info, a channel's recent messages, creating
reminders and polls, dice rolls, and (optionally) wiki and web search for
fact-checking.

**Reply presentation.** Every element is toggled independently in `.env`:
embeds colored by the channel's mode, a footer with stats (which tools were
called and how many tokens were spent), a 🔁 "Reroll" button (usable only by
the message's author) and a 🧹 "Forget conversation" button.

**Language guard** (`LANG_GUARD`) — a deterministic retry for the rare case
where the model answers in the wrong language despite its configured
persona.

**ZZZ advisor mode** (Zenless Zone Zero, `/mode`) — a local database of
agents, W-Engines, discs and bangboo with auto-injected context for
entities mentioned in a message (Cyrillic transliteration, Ukrainian case
inflection handled), a bangboo matcher for a given team composition, and
warnings about CN/West version discrepancies and stale data. `/zzz_reload`
reloads the database without restarting the bot.

Plus a classic set of server commands: moderation (`/purge` `/timeout`
`/kick` `/ban` `/warn` and others), persistent reminders (`/remind`
`/reminders`), native Discord polls (`/poll`), welcome messages for new
members, and MEE6-style XP levels (`/rank` `/leaderboard`) — the last two
are optional.

## Requirements

- **Python 3.11+**
- A [LongCat API Platform](https://longcat.chat/platform) key

## Installation

### 1. Create a Discord application

1. Open <https://discord.com/developers/applications> → **New
   Application**.
2. **Bot** tab → **Reset Token** → copy the token (this is your
   `DISCORD_TOKEN`).
3. Further down on the same tab, under **Privileged Gateway Intents**,
   enable both and click Save:
   - **MESSAGE CONTENT INTENT** — without it the bot can't see message
     text.
   - **SERVER MEMBERS INTENT** — needed for welcome messages and member
     lookups.

### 2. Invite the bot to a server

**OAuth2 → URL Generator** tab:

- Scopes: `bot` + `applications.commands`.
- Bot Permissions: for your own server, **Administrator** is simplest;
  the minimal set is View Channels, Send Messages, Send Messages in
  Threads, Embed Links, Attach Files, Add Reactions, Read Message History,
  Manage Messages, Moderate Members, Kick Members, Ban Members, Manage
  Channels, Create Polls.
- Open the generated link and add the bot to your server.

### 3. Configure and run

```
:: Windows
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
:: fill in DISCORD_TOKEN and LONGCAT_API_KEY in .env
py bot.py
```

Linux/macOS: `python3 -m venv .venv && source .venv/bin/activate`, then the
same steps. In PyCharm: Settings → Project → Python Interpreter → select
`.venv`.

**Important:** set your server ID in `GUILD_IDS` — slash commands then
appear instantly; without it, global sync takes up to an hour. To find the
ID: enable Developer Mode (User Settings → Advanced), right-click the
server name → Copy Server ID. For multiple servers, list the IDs
comma-separated.

## How the chat works

- **Triggers:** @mentioning the bot, replying to its message, or any
  message in DMs. `/reset` clears a channel's memory, `/context` shows how
  much of it is in use.
- **Memory:** SQLite, kept separately per channel and per thread. Each
  request to the model includes the most recent messages within
  `CHAT_HISTORY_TOKEN_LIMIT` tokens, with the oldest dropped first.
  LongCat's context window is 1M tokens, but history is resent in full with
  every request, so this limit is the main lever for controlling daily
  quota usage.
- **Tools:** the tool-calling loop is capped by `CHAT_MAX_TOOL_ITERATIONS`;
  on the final iteration no tools are offered, forcing the model to answer
  in text. Tool errors are returned to the model as text, so it can
  self-correct. Wiki and web search are toggled by `WEB_TOOLS_ENABLED`.
- **Presentation:** `EMBED_REPLIES` — replies as embeds (color set by
  channel mode), `FOOTER_STATS` — a footer with tools called and tokens
  spent, `REPLY_BUTTONS` — 🔁 (reroll, author-only) and 🧹 (forget
  conversation) buttons.
- **Language guard:** `LANG_GUARD=ru` — if a reply comes back entirely in
  Ukrainian, the bot does one corrective retry in Russian (a deterministic
  heuristic based on the letters і/ї/є/ґ; system/utility text is excluded
  from the retry).
- **ZZZ mode:** `/mode` switches a channel into the ZZZ advisor. In this
  mode, ZZZ-specific tools and deterministic auto-injected context for
  entities mentioned in the message (📦 markers in the footer) are added to
  the prompt. The database is local JSON under `data/zzz/` (generated
  separately via `zzz/build_db.py`; only read at runtime).
- **Safety:** the LLM deliberately has no moderation tools. Banning,
  kicking and purging a channel are only available via slash commands,
  which check Discord permissions.
- **Quota:** token usage for every request is written to the log
  (`LLM usage: prompt=…, completion=…`) — handy for tracking spend and for
  data to include in LongCat's feedback form.
- Each channel processes one request at a time (further requests queue, the
  bot shows ⏳); `LLM_MAX_CONCURRENCY` caps the global number of concurrent
  API requests.

## Configuration (.env)

| Variable | Default | What it does |
| --- | --- | --- |
| `DISCORD_TOKEN` | — | bot token (required) |
| `LONGCAT_API_KEY` | — | LongCat API key (required) |
| `LONGCAT_BASE_URL` | `https://api.longcat.chat/openai/v1` | OpenAI-compatible endpoint |
| `LONGCAT_MODEL` | `LongCat-2.0` | model identifier |
| `LONGCAT_MAX_TOKENS` | `2048` | max tokens per reply |
| `LONGCAT_TEMPERATURE` | `0.7` | 0–1 |
| `LONGCAT_THINKING` | `false` | `true`/`false`/empty (don't send the parameter) |
| `CHAT_HISTORY_TOKEN_LIMIT` | `24000` | how much history is sent with each request |
| `CHAT_MAX_TOOL_ITERATIONS` | `6` | cap on tool-loop steps |
| `LLM_MAX_CONCURRENCY` | `2` | concurrent requests to LongCat |
| `CHAT_SYSTEM_PROMPT` | built-in | your own system prompt |
| `WEB_TOOLS_ENABLED` | `true` | wiki + web search as LLM tools |
| `EMBED_REPLIES` | `true` | replies as embeds |
| `FOOTER_STATS` | `true` | footer with tools/token stats |
| `REPLY_BUTTONS` | `true` | 🔁/🧹 buttons under a reply |
| `LANG_GUARD` | — | `ru` = retry fully-Ukrainian replies; empty = disabled |
| `GUILD_IDS` | — | server IDs for instant command sync |
| `WELCOME_CHANNEL_ID` | — | welcome-message channel (empty = disabled) |
| `LEVELS_ENABLED` | `true` | XP system |
| `DATABASE_PATH` / `LOG_LEVEL` / `LOG_FILE` | `bot.db` / `INFO` / `bot.log` | self-explanatory |

## Structure

```
longcat-discord-bot/
├── bot.py              # entry point: intents, cogs, sync, error handling
├── config.py           # .env loading
├── database.py         # aiosqlite: history, reminders, warns, XP, channel modes
├── utils.py            # 2000-char splitter, fix_tables, time parsing, dice
├── llm/
│   ├── client.py       # async LongCat client: semaphore, backoff retries
│   ├── memory.py       # system prompt + token-based history trimming
│   └── tools.py        # tool schemas/registry (incl. web tools), agentic loop
├── zzz/
│   ├── db.py           # ZZZ database interface for the LLM (search/describe/auto_context/bangboo)
│   ├── tools.py        # ZZZ mode: prompt and tool schemas
│   └── build_db.py     # offline JSON database generator from hakushin (not needed at runtime)
├── cogs/
│   ├── chat.py          # LLM chat (mention/reply/DM), embeds/buttons, language guard, /reset, /context
│   ├── zzz.py            # /mode, /zzz_reload
│   ├── utility.py  moderation.py  fun.py
│   ├── polls.py  reminders.py  welcome.py  levels.py
└── tests/               # pytest: utils, database, memory, tools, zzz.db, zzz.build_db, chat, levels
```

## Testing

```
:: inside the activated .venv
pip install pytest pytest-asyncio
pytest -q
```

Covers pure logic with no network calls (network calls are mocked):
`utils`, `database`, `llm.memory`, `llm.tools`, `zzz.db`, `zzz.build_db`,
`cogs.chat`, `cogs.levels`. No API key or real data needed.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `PrivilegedIntentsRequired` on startup | Enable MESSAGE CONTENT + SERVER MEMBERS in the Dev Portal → Bot tab |
| Slash commands don't show up | Set `GUILD_IDS` and restart the bot; reload the Discord client (Ctrl+R). Global sync takes up to 1 hour |
| Bot doesn't respond to replies without a ping | MESSAGE CONTENT INTENT isn't enabled |
| Garbled text in the Windows console | Display-only issue: the full log in `bot.log` is UTF-8. If needed, `set PYTHONIOENCODING=utf-8` |
| `LongCat API 401` | Wrong `LONGCAT_API_KEY` |
| Frequent 429s | Quota/rate limit: backoff retries are already built in; lower `LLM_MAX_CONCURRENCY`, check your quota on the platform |
| `LongCat API 400` mentioning `tools` | The endpoint rejected function calling — temporarily drop tools (pass `tools=None` in `run_agent`) or switch the client to the Anthropic-compatible endpoint `/anthropic/v1` |
| Timeout/kick/ban "role too low" | Move the bot's role above the target's: Server Settings → Roles |
| Poll rejected | Discord limits: question ≤300 chars, 2–10 options ≤55 chars each, duration ≤768 hours |

## Changelog

Notable changes are tracked in [`CHANGELOG.md`](CHANGELOG.md).

## Ideas for later

- Reaction roles and auto-role for new members
- `/persona` — switch system prompts on the fly
- Export a channel's conversation to a file — ready-made material for
  LongCat's feedback form
- Daily token-spend counter in the DB + a `/quota` command
- Daily backup of `bot.db` + curated ZZZ data

## License

[MIT](LICENSE)
