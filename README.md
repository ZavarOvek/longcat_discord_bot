# 🐈‍⬛ LongCat Discord Bot

Персональний Discord-бот на Python: LLM-чат на базі **Meituan LongCat-2.0**
(OpenAI-сумісний API) з інструментами й пам'яттю розмов + класичний набір
серверних фіч (модерація, нагадування, опитування, рівні). Запускається
локально, все зберігає в SQLite поруч із собою.

## Можливості

- 🤖 **Чат з LongCat** — @згадай бота, відповідай реплаєм на його повідомлення або пиши в DM.
  Пам'ять розмови окрема для кожного каналу/гілки, переживає рестарт.
  У моделі є інструменти: поточний час, інфо про сервер/користувача,
  останні повідомлення каналу, створення нагадувань та опитувань, кубики,
  а також (опційно) **вікі та веб-пошук** для перевірки фактів.
- 🎨 **Оформлення відповідей** (кожне вимикається у `.env`): ембеди з кольором за режимом
  каналу, футер зі статистикою (викликані тули + витрачені токени), кнопки
  🔁 «Переролити» (лише автор запиту) і 🧹 «Забути розмову».
- 🌐 **Мовний страж** (`LANG_GUARD`) — детермінований ретрай, якщо модель зісковзнула
  на мову співрозмовника попри персону.
- ⚡ **Режим ZZZ-радника** (Zenless Zone Zero) — `/mode`: локальна база агентів,
  W-Engine, дисків і банбу; авто-підкладка даних по згаданих сутностях
  (з транслітерацією кирилиці й відмінюванням), матчер банбу під склад команди,
  попередження про CN/West-розбіжності та застарілі дані. `/zzz_reload` перечитує базу.
- 🛠 Утиліти: `/ping` `/serverinfo` `/userinfo` `/avatar` `/help`
- 🛡 Модерація: `/purge` `/timeout` `/untimeout` `/kick` `/ban` `/unban` `/warn` `/warns` `/clearwarns` `/slowmode`
- 📊 `/poll` — нативні опитування Discord
- ⏰ `/remind` `/reminders` `/reminder_delete` — персистентні нагадування
- 👋 Привітання новачків (опційно, `WELCOME_CHANNEL_ID`)
- 🏆 XP-рівні як у MEE6: `/rank` `/leaderboard` (опційно, `LEVELS_ENABLED`)

## Вимоги

- **Python 3.11+**
- Ключ [LongCat API Platform](https://longcat.chat/platform)

## Установка

### 1. Створи Discord-застосунок

1. <https://discord.com/developers/applications> → **New Application**
2. Вкладка **Bot** → **Reset Token** → скопіюй токен (це `DISCORD_TOKEN`)
3. Там само нижче, **Privileged Gateway Intents** — увімкни **обидва** і натисни Save:
   - ✅ **MESSAGE CONTENT INTENT** — без нього бот не бачить текст повідомлень
   - ✅ **SERVER MEMBERS INTENT** — привітання новачків, пошук учасників

### 2. Запроси бота на сервер

Вкладка **OAuth2 → URL Generator**:

- Scopes: `bot` + `applications.commands`
- Bot Permissions: для власного сервера найпростіше **Administrator**; мінімальний набір —
  View Channels, Send Messages, Send Messages in Threads, Embed Links, Attach Files,
  Add Reactions, Read Message History, Manage Messages, Moderate Members, Kick Members,
  Ban Members, Manage Channels, Create Polls
- Відкрий згенерований лінк і додай бота на сервер.

### 3. Налаштуй і запусти

```bat
:: Windows
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
:: заповни DISCORD_TOKEN і LONGCAT_API_KEY у .env
py bot.py
```

Linux/macOS: `python3 -m venv .venv && source .venv/bin/activate`, далі так само.
У PyCharm: Settings → Project → Python Interpreter → обери `.venv`.

**Важливо:** впиши ID свого сервера в `GUILD_IDS` — slash-команди з'являться миттєво.
Без цього глобальний sync триває до години. ID сервера: увімкни Developer Mode
(User Settings → Advanced), ПКМ по назві сервера → Copy Server ID. Додаєш бота
на другий сервер — просто допиши його ID через кому і перезапусти.

## Як працює чат

- **Тригери:** @згадка бота, реплай на його повідомлення, будь-яке повідомлення в DM.
  `/reset` очищає пам'ять каналу, `/context` показує її обсяг.
- **Пам'ять:** SQLite, окремо на кожен канал і кожну гілку. У запит до моделі йдуть
  останні повідомлення в межах `CHAT_HISTORY_TOKEN_LIMIT` токенів, найстаріші відкидаються.
  Вікно LongCat — 1M токенів, але історія пересилається заново з кожним запитом,
  тож саме цей ліміт — головний регулятор витрат денної квоти.
- **Інструменти:** tool-цикл обмежений `CHAT_MAX_TOOL_ITERATIONS`; на останній ітерації
  інструменти не передаються, і модель мусить відповісти текстом. Помилки інструментів
  повертаються моделі текстом — вона може виправитись сама. Вікі й веб-пошук
  вмикаються `WEB_TOOLS_ENABLED`.
- **Оформлення:** `EMBED_REPLIES` — відповіді в ембедах (колір за режимом каналу),
  `FOOTER_STATS` — футер із викликаними тулами й витраченими токенами,
  `REPLY_BUTTONS` — кнопки 🔁 (переролити, лише автор) і 🧹 (забути розмову).
- **Мовний страж:** `LANG_GUARD=ru` — якщо відповідь вийшла повністю українською,
  бот робить один коригувальний ретрай російською (детермінована евристика за
  літерами і/ї/є/ґ, службові тексти в ретрай не потрапляють).
- **Режим ZZZ:** `/mode` перемикає канал у ZZZ-радника. У цьому режимі до промпта
  підключаються ZZZ-інструменти й детермінована авто-підкладка даних по сутностях
  з повідомлення (мітки 📦 у футері). База — локальні JSON у `data/zzz/`
  (генерується окремо через `zzz/build_db.py`, у ран-таймі лише читається).
- **Безпека:** модераційних інструментів у LLM немає навмисно. Банити, кікати й чистити
  канал можна лише slash-командами з перевіркою прав Discord.
- **Квота:** використання токенів кожного запиту пишеться в лог
  (`LLM usage: prompt=…, completion=…`) — зручно стежити за витратами і брати
  дані для фідбек-форми LongCat.
- Один канал обробляє один запит за раз (запити стають у чергу, бот ставить ⏳);
  глобальну кількість одночасних запитів до API обмежує `LLM_MAX_CONCURRENCY`.

## Налаштування (.env)

| Змінна | Типово | Що робить |
| --- | --- | --- |
| `DISCORD_TOKEN` | — | токен бота (обов'язково) |
| `LONGCAT_API_KEY` | — | ключ LongCat (обов'язково) |
| `LONGCAT_BASE_URL` | `https://api.longcat.chat/openai/v1` | OpenAI-сумісний ендпоінт |
| `LONGCAT_MODEL` | `LongCat-2.0` | ідентифікатор моделі |
| `LONGCAT_MAX_TOKENS` | `2048` | стеля токенів однієї відповіді |
| `LONGCAT_TEMPERATURE` | `0.7` | 0–1 |
| `LONGCAT_THINKING` | `false` | `true`/`false`/порожньо (не надсилати параметр) |
| `CHAT_HISTORY_TOKEN_LIMIT` | `24000` | скільки історії їде в кожен запит |
| `CHAT_MAX_TOOL_ITERATIONS` | `6` | ліміт кроків tool-циклу |
| `LLM_MAX_CONCURRENCY` | `2` | одночасні запити до LongCat |
| `CHAT_SYSTEM_PROMPT` | вбудований | свій системний промпт |
| `WEB_TOOLS_ENABLED` | `true` | вікі + веб-пошук як інструменти LLM |
| `EMBED_REPLIES` | `true` | відповіді в ембедах |
| `FOOTER_STATS` | `true` | футер зі статистикою тулів/токенів |
| `REPLY_BUTTONS` | `true` | кнопки 🔁/🧹 під відповіддю |
| `LANG_GUARD` | — | `ru` = ретраїти повністю українські відповіді; порожньо = вимкнено |
| `GUILD_IDS` | — | ID серверів для миттєвого sync команд |
| `WELCOME_CHANNEL_ID` | — | канал привітань (порожньо = вимкнено) |
| `LEVELS_ENABLED` | `true` | XP-система |
| `DATABASE_PATH` / `LOG_LEVEL` / `LOG_FILE` | `bot.db` / `INFO` / `bot.log` | очевидне |

## Структура

```
longcat-discord-bot/
├── bot.py              # точка входу: інтенти, cogs, sync, обробка помилок
├── config.py           # завантаження .env
├── database.py         # aiosqlite: історія, нагадування, warn, XP, режими каналів
├── utils.py            # сплітер 2000 символів, fix_tables, парсинг часу, кубики
├── llm/
│   ├── client.py       # async LongCat-клієнт: семафор, ретраї з бекофом
│   ├── memory.py       # системний промпт + тримінг історії за токенами
│   └── tools.py        # схеми/реєстр інструментів (з веб-тулами), агентний цикл
├── zzz/
│   ├── db.py           # інтерфейс до ZZZ-баз для LLM (search/describe/auto_context/bangboo)
│   ├── tools.py        # ZZZ-режим: промпт і схеми інструментів
│   └── build_db.py     # офлайн-генератор JSON-баз із hakushin (у ран-таймі не потрібен)
├── cogs/
│   ├── chat.py         # LLM-чат (згадка/реплай/DM), ембеди/кнопки, мовний страж, /reset, /context
│   ├── zzz.py          # /mode, /zzz_reload
│   ├── utility.py  moderation.py  fun.py
│   ├── polls.py  reminders.py  welcome.py  levels.py
└── tests/              # pytest: utils, database, memory, tools, zzz.db, zzz.build_db, chat, levels
```

## Тести

```bat
:: у активованому .venv
pip install pytest pytest-asyncio
pytest -q
```

Покривають чисту логіку без мережі (мережеві виклики мокнуті): `utils`, `database`,
`llm.memory`, `llm.tools`, `zzz.db`, `zzz.build_db`, `cogs.chat`, `cogs.levels`.
Ключ і реальні дані для тестів не потрібні.

## Траблшутінг

| Симптом | Причина / рішення |
| --- | --- |
| `PrivilegedIntentsRequired` при старті | Увімкни MESSAGE CONTENT + SERVER MEMBERS у Dev Portal → Bot |
| Slash-команди не з'являються | Впиши `GUILD_IDS` і перезапусти бота; перезавантаж клієнт Discord (Ctrl+R). Глобальний sync — до 1 години |
| Бот не реагує на реплаї без пінга | Не ввімкнений MESSAGE CONTENT INTENT |
| Кракозябри в консолі Windows | Лише відображення: повний лог у `bot.log` (UTF-8). За бажання `set PYTHONIOENCODING=utf-8` |
| `LongCat API 401` | Невірний `LONGCAT_API_KEY` |
| Часті 429 | Квота/ліміт запитів: ретраї з бекофом уже вбудовані; зменш `LLM_MAX_CONCURRENCY`, перевір квоту на платформі |
| `LongCat API 400` зі згадкою `tools` | Ендпоінт відмовив у function calling — тимчасово можна прибрати інструменти (передавати `tools=None` у `run_agent`) або перевести клієнт на Anthropic-сумісний ендпоінт `/anthropic/v1` |
| Timeout/kick/ban «роль нижча» | Підніми роль бота вище цілі: Server Settings → Roles |
| Опитування відхилено | Ліміти Discord: питання ≤300 символів, 2–10 варіантів ≤55 символів, тривалість ≤768 год |

## Ідеї на потім

- Reaction roles і авто-роль новачкам
- `/persona` — перемикання системних промптів на льоту
- Експорт діалогу канала в файл — готовий матеріал для фідбек-форми LongCat
- Daily-лічильник витрачених токенів у БД + команда `/quota`
- Щоденний бекап `bot.db` + curated-даних ZZZ
