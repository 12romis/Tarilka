# Сімейні страви — план MVP (FastAPI + aiogram + Postgres)

Nov 22, 2026 · @Ігор

## Мета і принципи

MVP — це сімейний каталог страв, які ми вже вміємо готувати. Головний сценарій — кнопка «Що приготувати?», яка за 2 секунди пропонує 3 страви з урахуванням фільтрів. Документ написано для агента, який розроблятиме й розгортатиме проєкт на VPS, де вже є FastAPI, aiogram і PostgreSQL.

**Проблема.** Коли треба вирішити, що готувати, ніхто не може згадати, що ми вміємо, тож у підсумку нічого конкретного не готується. Отже, головна цінність — швидко наповнити базу і швидко з неї вибрати. Повнота рецептів — другорядна.

**Принципи MVP**

- **Страва ≠ рецепт.** Страву можна зберегти лише з назвою («Деруни, вміє мама»). Рецепт, інгредієнти й фото необов'язкові.
- **Два статуси:** `can_cook` («вміємо») і `want_to_try` («хочемо спробувати»). Імпорт за посиланням за замовчуванням дає `want_to_try`. Кнопка «Приготували ✅» переводить страву в `can_cook`.
- **Додавання — лише через бота**, одним повідомленням: текст, голос, посилання або фото. AI готує чернетку, людина підтверджує її кнопками.
- **Перегляд і вибір — через Telegram Mini App** на власному домені. Ту саму сторінку можна відкрити у звичайному браузері.
- **AI тільки на етапі вводу.** Він розбирає текст, теги й відео. Пошук і фільтри працюють на звичайному SQL.
- **Одна сім'я, закрите коло.** Доступ лише за білим списком Telegram ID.

**Що НЕ робимо в MVP:** планувальник меню на тиждень, список покупок, калорії, публічні акаунти, Android-застосунок, коментарі й рейтинги від кількох користувачів (достатньо однієї оцінки на страву).

## Архітектура

Один репозиторій і один Docker-образ з двома процесами. `app` обробляє вебхук бота, REST API й роздає статику Mini App. `worker` виконує довгі задачі імпорту. Черга задач — таблиця `import_jobs` у Postgres, тож Redis не потрібен.

&#91;embedded content: архітектура MVP · 2 контейнери + наявний Postgres\]

Запити від Telegram і Mini App проходять через nginx до `app`. Усе, що триває довше за 1–2 секунди, `app` віддає `worker` через таблицю jobs. Відповідь у чат `worker` надсилає напряму через Bot API.

| Шар | Рішення |
| --- | --- |
| Python | 3.12, залежності через `uv` |
| Web/API | FastAPI + Uvicorn |
| Бот | aiogram 3, режим webhook, той самий процес FastAPI; FSM-сховище в Postgres або Memory (стан чернеток живе в БД) |
| БД | наявний PostgreSQL, окрема база `dishes` і окремий користувач; SQLAlchemy 2 (async, asyncpg) + Alembic; розширення `pg_trgm` для пошуку |
| Черга | таблиця `import_jobs` + `SELECT … FOR UPDATE SKIP LOCKED`, опитування раз на 2 с |
| Імпорт | `recipe-scrapers`, `extruct` (резерв для JSON-LD), `httpx`, `yt-dlp`, `ffmpeg` |
| AI | один модуль `ai/` з провайдером із `.env`; LLM із structured output (JSON Schema), STT через API (Whisper-сумісний) |
| Mini App | Vite + Preact (або React) + TypeScript, `@telegram-apps/sdk`; збірка у статику |
| Фото | диск VPS, volume `/data/media`, прев'ю через Pillow (WebP, 1200 px і 400 px) |

**Структура репозиторію**

```
family-dishes/
  app/
    main.py            # FastAPI, монтує webhook, /api, статику
    config.py          # pydantic-settings
    db/ models.py session.py
    api/ dishes.py suggest.py meta.py auth.py
    bot/ router.py handlers/ keyboards.py cards.py
    importers/ base.py web.py video.py text.py photo.py mealie.py
    ai/ client.py prompts.py schemas.py
    services/ dishes.py suggest.py media.py jobs.py
    worker.py          # цикл обробки import_jobs
  migrations/          # alembic
  webapp/              # Mini App (Vite)
  tests/
  Dockerfile  docker-compose.yml  .env.example  README.md
```

## Модель даних

У центрі таблиця `dishes`. Усе, за чим фільтруємо, — це довідники зі зв'язками many-to-many, щоб страва могла бути і «сніданок», і «перекус». Усі таблиці мають `family_id`, щоб пізніше можна було додати інші сім'ї. В MVP сім'я одна, її створює seed.

| Таблиця | Ключові поля |
| --- | --- |
| `families` | id, name, created\_at |
| `members` | id, family\_id, name («Ігор», «Мама»), tg\_user\_id (nullable — не всі мають Telegram), is\_admin, aliases text\[\] («мама», «мамуся» — для AI-розбору) |
| `dishes` | id, family\_id, title, description, status enum(`can_cook`, `want_to_try`, `archived`), main\_ingredient\_id, cook\_time\_min, difficulty smallint 1–3, servings, ingredients jsonb `[{name, qty, unit}]`, steps jsonb `[text]`, notes, rating smallint 1–5, source\_url, source\_type enum(`manual`, `web`, `tiktok`, `instagram`, `youtube`, `photo`, `voice`), source\_author, raw\_import jsonb, last\_cooked\_at, cooked\_count, created\_by, created\_at, updated\_at |
| `dish_images` | id, dish\_id, path, path\_thumb, position, is\_cover, origin (`og_image`, `video_frame`, `upload`) |
| `dish_cooks` | dish\_id, member\_id — хто вміє готувати |
| `tags` | id, family\_id, kind enum(`meal`, `course`, `ingredient`, `cuisine`, `trait`), slug, label, emoji, sort |
| `dish_tags` | dish\_id, tag\_id |
| `cook_log` | id, dish\_id, member\_id, cooked\_at, note — з нього рахуються last\_cooked\_at і cooked\_count |
| `import_jobs` | id, family\_id, kind (`url`, `text`, `voice`, `photo`), payload jsonb, status (`queued`, `running`, `draft_ready`, `failed`, `done`), attempts, error, result jsonb (чернетка), dish\_id, tg\_chat\_id, tg\_message\_id, created\_at, updated\_at |

`main_ingredient_id` — це FK на `tags` з kind=`ingredient`. Основний продукт один. Додаткові продукти зберігаються як звичайні теги kind=`ingredient` у `dish_tags`.

**Seed-довідники** (редагуються пізніше):

- **meal:** сніданок, обід, вечеря, перекус
- **course:** суп, салат, основна страва, гарнір, випічка, десерт, соус, напій, заготовка
- **ingredient:** курка, свинина, яловичина, фарш, риба, морепродукти, яйця, сир і творог, картопля, крупи, макарони, бобові, овочі, гриби, борошно
- **trait:** швидко (≤ 30 хв), на свято, для дітей, дешево, пісне, в духовці, можна заморозити
- **cuisine:** українська, італійська, азійська, грузинська, інша

**Індекси:** `dishes(family_id, status)`, GIN trigram на `dishes.title`, `dish_tags(tag_id)`, `dish_cooks(member_id)`, `import_jobs(status, created_at)`, унікальний індекс `(family_id, source_url)` WHERE source\_url IS NOT NULL — захист від повторного імпорту того самого посилання.

## Telegram-бот

Бот приймає будь-яке повідомлення без команд і сам визначає його тип. На кожне повідомлення він відповідає карткою-чернеткою з кнопками. Після «Зберегти» страва потрапляє в каталог.

**Доступ.** Middleware перевіряє `from_user.id` за `members.tg_user_id`. Чужим бот відповідає «Бот приватний» і далі їх ігнорує. Адмін запрошує членів сім'ї командою `/invite`, яка створює deep link `t.me/<bot>?start=inv_<token>` з терміном дії 24 години. Перший адмін задається через `ADMIN_TG_IDS` у `.env`. Бот працює і в сімейній групі: там він реагує лише на посилання, згадки `@bot` і відповіді на власні повідомлення.

**Команди та меню**

| Команда | Дія |
| --- | --- |
| Кнопка меню «🍽 Страви» | відкриває Mini App (`setChatMenuButton`, тип web\_app) |
| `/start` | вітання, коротка підказка «надішли назву, голосове або посилання», обробка інвайтів |
| `/what` або текст «що приготувати» | 3 пропозиції прямо в чат; inline-кнопки фільтрів типу прийому їжі та «🎲 Ще» |
| `/find <текст>` | пошук за назвою (trigram), до 10 результатів |
| `/cooked` | позначити приготовану страву (пошук + кнопка) |
| `/invite`, `/members` | лише для адміна: інвайти та члени сім'ї без Telegram (наприклад, бабуся) |

**Сценарії додавання** (тип визначає один роутер на основі фільтрів aiogram):

1. **Посилання** (entity `url` або `text_link`, також переслане з TikTok чи Instagram). Бот відповідає «⏳ Імпортую…» і створює `import_jobs(kind=url)`. Worker редагує це повідомлення, замінюючи його карткою-чернеткою. Якщо `source_url` уже є в базі, бот одразу показує наявну страву.
2. **Текст без посилання** («деруни з грибами, вміє мама, на вечерю»). Підтримується список страв через кому або з нового рядка. Задача `kind=text` — це один виклик LLM, який повертає масив чернеток. Якщо страв кілька, бот показує одну зведену картку «Знайшов 5 страв» з кнопками «Зберегти все» і «Переглянути по одній». Це головний спосіб швидко наповнити базу на старті.
3. **Голосове або аудіо.** Бот завантажує .ogg, STT перетворює його на текст, далі все йде як у п. 2. У картці показується розпізнаний текст, щоб було видно, що чув бот.
4. **Фото** (скріншот рецепта, сторінка книги або фото готової страви) з підписом або без нього. Фото уходить у vision-LLM. Якщо на ньом рецепт, створюється чернетка. Якщо це фото страви, бот пропонує «Додати фото до наявної страви» (пошук за назвою) або «Нова страва».

**Картка-чернетка** — це повідомлення з фото чи альбомом і підписом:

```
🥞 Деруни з грибами
👩 Мама  ·  🌙 Вечеря  ·  🥔 Картопля  ·  ⏱ 40 хв
Статус: вміємо
Джерело: tiktok.com/@…
```

Inline-кнопки картки: `✅ Зберегти` · `👤 Хто вміє` · `🏷 Теги` · `✏️ Назва` · `🔁 Вміємо / Хочемо спробувати` · `📝 Редагувати в вітрині` (web\_app-кнопка на `/dish/<id>/edit`) · `🗑 Відкинути`.

- «Хто вміє» і «Теги» — це меню-перемикачі з позначками ☑️ та кнопкою «← Назад»; вони редагують те саме повідомлення.
- Всі callback-дані мають вигляд `CallbackData` aiogram (`draft:<job_id>:<action>[:<arg>]`) і вміщуються в 64 байти. Чернетка зберігається в `import_jobs.result`, а не в FSM.
- Поле «Хто вміє» за замовчуванням заповнюється автором повідомлення, якщо AI не знайшов іншого імені в тексті. Для відео-імпорту воно залишається порожнім.
- Якщо чернетку не підтвердили за 48 годин, вона зберігається автоматично зі статусом з чернетки. Посилання не має губитися.

**Після приготування.** У картці пропозиції (з `/what` або Mini App) є кнопка «👨‍🍳 Готую!». Вона пише запис у `cook_log`, переводить `want_to_try` у `can_cook` і додає автора в «Хто вміє». Далі бот одним повідомленням пропонує оцінити страву від 1 до 5 або прикріпити фото.

## Імпорт

Усі джерела сходяться до одного формату `DishDraft`, і кожна чернетка проходить через один LLM-крок нормалізації. Імпортери — це ланцюжок провайдерів із спільним інтерфейсом. Якщо провайдер не спрацював, запускається наступний. Останній резерв — чернетка лише з посиланням і og-метаданими, тож посилання не губиться ніколи.

```python
class Importer(Protocol):
    name: str

    def can_handle(self, src: ImportSource) -> bool: ...
    async def extract(self, src: ImportSource) -> RawExtract | None: ...


# RawExtract: title, text_blocks[] (опис, субтитри, транскрипт),
#             structured (schema.org Recipe, якщо є), image_urls[], local_images[],
#             source_type, source_author, confidence
```

**Ланцюжок для посилань**

1. **Нормалізація URL.** Розгорнути короткі посилання (`vm.tiktok.com`, `vt.tiktok.com`, `youtu.be`) через HEAD/GET із follow redirects. Прибрати utm-параметри та визначити `source_type` за доменом.
2. **`WebRecipeImporter`** — для всіх доменів, крім відеоплатформ. Сторінка завантажується через `httpx` з браузерним User-Agent, таймаут 15 с. Далі застосовується `recipe_scrapers.scrape_html(html, org_url=url, supported_only=False)`. Якщо це не спрацювало, `extruct` шукає JSON-LD `@type: Recipe`. Якщо й це не спрацювало, береться чистий текст сторінки (`trafilatura` або `readability-lxml`) обсягом до 12 000 символів для LLM. Фото беруться з `image` у schema.org та з `og:image`.
3. **`VideoImporter`** — TikTok, Instagram, YouTube (включно з Shorts) і Facebook. Команди див. нижче.
4. **`MealieImporter`** (опційний, `MEALIE_ENABLED=true`) — резерв, якщо п.2 або п.3 повернули `confidence < 0.5`.
5. **`LinkOnlyImporter`** — og:title, og:image і og:description. Дає чернетку зі статусом `want_to_try` і приміткою «рецепт не розпізнано».

**Відео-пайплайн** (дешевий крок спочатку, дорогий лише за потреби):

1. `yt-dlp --skip-download --write-info-json --write-subs --write-auto-subs --sub-langs "uk.*,ru.*,en.*" --write-thumbnail -o "/tmp/imp/<job>/%(id)s.%(ext)s" <url>` — отримує підпис (`description`), автора (`uploader`), обкладинку, субтитри та `duration`. Викликати через Python API `yt_dlp.YoutubeDL`, а не через subprocess-рядок.
2. Підпис і субтитри уходять у LLM з питанням, чи достатньо даних. Якщо `has_recipe=true` і `confidence ≥ 0.7`, відео не завантажуємо.
3. Інакше завантажуємо відео, якщо `duration ≤ 600 с` і розмір ≤ 150 МБ. Звук витягуємо через ffmpeg (`-vn -ac 1 -ar 16000 -b:a 32k` у .mp3) для STT. Транскрипт додаємо до `text_blocks`.
4. **Кадри для фото:** ffmpeg бере кадри на 20%, 50% і 85% тривалості (`-ss <t> -frames:v 1`) плюс обкладинка, до 4 зображень. Обкладинка стає cover. Тимчасові файли видаляються після задачі. Саме відео не зберігаємо.
5. **Instagram** часто вимагає кукі. Треба підтримати `YTDLP_COOKIES_FILE` (Netscape-формат, експорт з браузера). Якщо куків немає і сталася помилка, переходимо до кроку 4 або 5 ланцюжка.
6. `yt-dlp` ламається після змін на платформах. Тому в контейнері worker потрібен щоденний `pip install -U yt-dlp` (cron у контейнері або перевірка при старті). Версію треба логувати.

**Mealie — наскільки він перетинається з цим підходом.** Mealie підходить лише як двигун імпорту. Робити його місцем зберігання не варто, бо в нього інша модель: там немає понять «хто вміє» і «вміємо / хочемо». Власний пайплайн вище покриває те саме й не потребує ще одного сервісу, тому він основний. Mealie підключаємо як резерв, вимкнений за замовчуванням:

- Сервіс `mealie` додається в окремому compose-профілі `--profile mealie`, доступний лише з внутрішньої мережі Docker. Відео-імпорт з'явився в Mealie v3.13 і працює лише з налаштованою в Mealie інтеграцією OpenAI.
- Імпорт за посиланням: `POST {MEALIE_URL}/api/recipes/create/url` з `{"url": …, "include_tags": true}` і Bearer-токеном. Відповідь — slug. Потім `GET /api/recipes/{slug}`, мапінг у `RawExtract(structured=…)` і `DELETE /api/recipes/{slug}`, щоб у Mealie не накопичувалося сміття.
- **Завдання агенту:** точний ендпоінт відео-імпорту та формат тіла запиту взяти з OpenAPI саме розгорнутої версії (`{MEALIE_URL}/docs`). Не вгадувати.

**LLM-нормалізація → `DishDraft`** (один виклик, structured output за JSON Schema, temperature 0):

```json
{
  "has_recipe": true,
  "confidence": 0.0,
  "title": "Деруни з грибами",
  "description": "1–2 речення",
  "status": "can_cook | want_to_try",
  "cooks": ["member_id"],
  "meal": ["dinner"], "course": ["main"],
  "main_ingredient": "potato", "ingredient_tags": ["mushrooms"],
  "traits": ["quick"], "cuisine": "ukrainian",
  "cook_time_min": 40, "difficulty": 2, "servings": 4,
  "ingredients": [{"name": "картопля", "qty": 1, "unit": "кг"}],
  "steps": ["..."],
  "notes": null
}
```

Правила для промпту (файл `ai/prompts.py`):

- Відповідь завжди українською. Назви іншомовних страв перекладаються; загальновідомі назви типу «Тірамісу» залишаються як є.
- Теги — **лише зі списку slug**, який передається в промпт (підтягується з БД). Невідоме залишається порожнім, нові теги не вигадуються.
- `cooks` — лише id зі списку членів сім'ї з їхніми іменами і `aliases`.
- Кількості та час не вигадуються: якщо їх немає в джерелі, ставиться `null`.
- Для ручного текстового вводу за замовчуванням `status=can_cook`, для імпорту за посиланням — `want_to_try`.

**AI-провайдер.** `ai/client.py` надає три методи: `structured(prompt, schema, images=None)`, `transcribe(audio_path)` і `vision`. Модель і ключ беруться з `.env` (`LLM_PROVIDER`, `LLM_MODEL`, `STT_MODEL`). Досить швидкої дешевої моделі з vision і structured output. Кожен виклик логує токени й час у `import_jobs.result.meta`.

**Надійність worker.** Кожна задача має таймаут 5 хв. У разі помилки робиться до 2 повторів із затримкою 30 с і 2 хв. У статусі `failed` бот пише людську причину і все одно пропонує «Зберегти як посилання». Одночасно виконуються не більше 2 задач (`WORKER_CONCURRENCY`).

## Mini App: вітрина і «Що приготувати?»

Головний екран — велика кнопка «🎲 Що приготувати?» з рядком швидких фільтрів над нею. Каталог розташований нижче. Mini App відкривається з кнопки меню бота, з кнопки в картці або за прямим посиланням `t.me/<bot>/app`. Сторінки відкриваються через `startapp=dish_<id>`. Кольори беруться з `themeParams` Telegram, тож світла й темна теми працюють автоматично.

**Екрани**

| Екран | Вміст |
| --- | --- |
| Головна | чипи «Прийом їжі» (за замовчуванням визначається за часом: до 11:00 сніданок, 11–16 обід, після 16 вечеря), «Хто готує», «Швидко», «З новим» (додати `want_to_try`); велика кнопка; під нею стрічки «Давно не готували» і «Нещодавно додані» |
| Результат «Що приготувати» | 3 великі картки з фото, назвою, хто вміє, часом і «готували N днів тому»; кнопки «🎲 Інші 3» та «👨‍🍳 Готую!» на кожній картці; свайп вліво — «не сьогодні» |
| Каталог | пошук за назвою; фільтри: статус, прийом їжі, тип страви, основний продукт, хто вміє, особливості, час ≤ N хв; сортування: давно не готували / нові / за оцінкою / за абеткою; сітка карток 2 в ряд; фільтри зберігаються в URL query |
| Страва | галерея, теги, хто вміє, інгредієнти, кроки, нотатки, джерело (відкривається через `openLink`), історія приготувань; MainButton «👨‍🍳 Готую!» |
| Редагування | усі поля; теги — чипи з довідника; завантаження фото, вибір обкладинки; архівація; MainButton «Зберегти» |
| Налаштування (для адміна) | члени сім'ї та їхні синоніми, редагування довідників тегів |

**Алгоритм «Що приготувати?»** (`services/suggest.py`): зважений випадковий вибір без повторів серед кандидатів, що пройшли фільтри.

1. **Кандидати:** `status = can_cook` (також `want_to_try`, якщо увімкнено «З новим»). Виключаються страви, приготовані за останні 3 дні, і `exclude_ids` (вже показані в цій сесії або відхилені свайпом).
2. **Вага** = `freshness × rating_k × novelty_k`:
   - `freshness = clamp(days_since_last_cooked / 14, 0.3, 3)`; якщо страву ще ніколи не готували, дорівнює 2
   - `rating_k = 0.6 + 0.2 × rating` (без оцінки = 1.0)
   - `novelty_k = 0.7` для `want_to_try`, щоб нове з'являлося, але не домінувало
3. Вибір 3 страв без повторів (`random.choices` з видаленням). **Правило різноманітності:** якщо достатньо кандидатів, не більше однієї страви з одним `main_ingredient`.
4. Якщо кандидатів менше 3, показуємо ті, що є, і підказку «послабте фільтри» з кнопкою скидання. Якщо страв у базі менше 10, показуємо заклик «Додайте страви — надішліть боту список через кому».

Алгоритм — чиста функція з параметром `rng` для тестів. Ваги винесені в конфіг.

**Технічні деталі Mini App:** збірка Vite в `webapp/dist`, яку роздає FastAPI (`StaticFiles`, SPA-fallback на `index.html`) або напряму nginx. Усі запити до `/api` йдуть із заголовком `Authorization: tma <initData>`. Потрібна підтримка `BackButton`, `MainButton` і `HapticFeedback` на «Готую». Зображення завантажуються ліниво, в сітці показуються thumbs. Режим браузера поза Telegram додається на етапі 5 (див. «Етапи»).

## API (FastAPI)

Усі ендпоінти під `/api` вимагають валідного Telegram `initData`. Вони працюють завжди в межах сім'ї користувача. Бот і API викликають одні й ті самі функції з `services/`, тому логіка не дублюється.

**Авторизація.** Залежність `current_member` перевіряє підпис `initData` (HMAC-SHA256, ключ = HMAC("WebAppData", BOT\_TOKEN)) і `auth_date` не старше 24 годин. Після цього вона шукає `members.tg_user_id`. Якщо члена не знайдено, повертається 403. Можна взяти готову функцію `aiogram.utils.web_app.safe_parse_webapp_init_data`. Для локальної розробки є `DEV_AUTH_MEMBER_ID`, який працює лише при `ENV=dev`.

| Метод · шлях | Призначення |
| --- | --- |
| `GET /api/me` | поточний член сім'ї, чи є він адміном |
| `GET /api/meta` | члени сім'ї та всі теги, згруповані за kind, з кількістю страв (для чипів фільтрів) |
| `GET /api/dishes` | список з параметрами `q`, `status[]`, `meal[]`, `course[]`, `ingredient[]`, `cook[]`, `trait[]`, `max_time`, `sort` (`stale`, `new`, `rating`, `title`), `limit`, `cursor`; всередині групи фільтрів — OR, між групами — AND |
| `GET /api/dishes/{id}` | повна страва з фото та останніми 10 записами `cook_log` |
| `POST /api/dishes` · `PATCH /api/dishes/{id}` | створення та редагування |
| `DELETE /api/dishes/{id}` | м'яке видалення: `status=archived` |
| `POST /api/dishes/{id}/cooked` | запис у `cook_log`, оновлення `last_cooked_at` і `cooked_count`, перехід `want_to_try` → `can_cook`, додавання автора в «Хто вміє» |
| `POST /api/dishes/{id}/images` · `DELETE …/images/{img}` · `PATCH …/images/{img}` | завантаження (multipart, ≤ 10 МБ, JPEG/PNG/WebP/HEIC), видалення та вибір обкладинки |
| `POST /api/suggest` | тіло: фільтри + `exclude_ids` → 3 страви та `total_candidates` |
| `POST /api/import` | `{url}` або `{text}` → job id; це той самий імпорт, що й у боті, для поля «Додати за посиланням» у вітрині |
| `GET /api/import/{job_id}` | статус і чернетка (опитування раз на 2 с) |
| `PUT /api/tags/{id}` · `POST /api/tags` · `POST /api/members` | лише для адміна |
| `POST /tg/webhook` | вебхук aiogram, перевірка `X-Telegram-Bot-Api-Secret-Token` |
| `GET /media/...` | фото; у продакшні роздає nginx з volume |
| `GET /healthz` | стан БД і черги (кількість `queued` і `failed` за 24 години) |

Pydantic-схеми лежать в `api/schemas.py`. З OpenAPI генеруються TS-типи для Mini App (`openapi-typescript`).

## Розгортання на VPS

Застосунок розгортається на сабдомені (наприклад, `food.<домен>`) з HTTPS: Telegram вимагає HTTPS і для вебхука, і для Mini App. Перший крок агента — інвентаризація сервера. Існуючі сервіси чіпати не можна.

**0. Інвентаризація (тільки читання, звіт перед змінами)**

- Визначити, як запущені наявні Postgres, FastAPI і aiogram: Docker чи systemd. Дізнатися версію Postgres і де він слухає.
- З'ясувати, що тримає порти 80/443 (nginx, Caddy, Traefik) і як випускаються сертифікати. Новий сайт підключати тим самим способом.
- Перевірити вільні RAM і диск: worker з ffmpeg потребує приблизно 300–500 МБ під час імпорту. Наявність DNS-запису сабдомену перевірити заздалегідь: створити його має власник.

**1. База даних.** На наявному сервері створити роль `dishes` з паролем і базу `dishes` з власником `dishes`, потім виконати `CREATE EXTENSION IF NOT EXISTS pg_trgm;`. До інших баз доступу не давати. Міграції `alembic upgrade head` запускаються при старті контейнера `app`.

**2. docker-compose.yml** (скелет):

```yaml
services:
  app:
    build: .
    command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"
    env_file: .env
    volumes: ["media:/data/media"]
    ports: ["127.0.0.1:8090:8000"]
    restart: unless-stopped
  worker:
    build: .
    command: python -m app.worker
    env_file: .env
    volumes: ["media:/data/media", "./secrets:/secrets:ro"]
    restart: unless-stopped
  mealie:                      # опційно
    image: ghcr.io/mealie-recipes/mealie:latest
    profiles: ["mealie"]
    volumes: ["mealie:/app/data"]
    restart: unless-stopped
volumes: { media: {}, mealie: {} }
```

- `Dockerfile`: `python:3.12-slim` + `apt install ffmpeg` + `uv sync`. Mini App збирається окремим Node-стейджем (multi-stage) і копіюється в `/app/webapp/dist`.
- Якщо Postgres не в Docker, `DATABASE_URL` спрямовується на хост через `host.docker.internal` з `extra_hosts: host-gateway`. Якщо Postgres у Docker, треба підключитися до його мережі як `external`.

**3. Reverse proxy.** Усі запити на `food.<домен>` проксуються на `127.0.0.1:8090`, а `/media/` роздається напряму з volume з `expires 30d`. Потрібні `client_max_body_size 12m` і TLS через Let's Encrypt тим самим способом, що й для інших сайтів.

**4. Telegram.** У @BotFather створити нового бота (це робить власник, агент просить токен). `/newapp` або «Configure Mini App» з URL `https://food.<домен>/`. При старті `app` викликає `setWebhook(url=…/tg/webhook, secret_token=WEBHOOK_SECRET, allowed_updates=[message, callback_query, my_chat_member])`, `setMyCommands` і `setChatMenuButton(web_app)`.

**5. `.env.example`**

```
ENV=prod
BOT_TOKEN=
WEBHOOK_SECRET=            # випадковий рядок 32+ символи
PUBLIC_BASE_URL=https://food.example.com
ADMIN_TG_IDS=123456789
DATABASE_URL=postgresql+asyncpg://dishes:***@host.docker.internal:5432/dishes
MEDIA_DIR=/data/media
LLM_PROVIDER=openai        # або anthropic / gemini
LLM_API_KEY=
LLM_MODEL=
STT_MODEL=
WORKER_CONCURRENCY=2
YTDLP_COOKIES_FILE=/secrets/cookies.txt   # опційно
MEALIE_ENABLED=false
MEALIE_URL=http://mealie:9000
MEALIE_API_TOKEN=
```

**6. Бекапи та обслуговування**

- Щодня о 04:00 запускати `pg_dump -Fc dishes` у `/var/backups/dishes/` (зберігати 14 копій) і `tar` для volume `media` раз на тиждень. Якщо на сервері вже є система бекапів, використати її.
- Команда адміна `/export` надсилає JSON усієї бази в чат: це простий власний бекап.
- Текстові логи йдуть у stdout (`docker logs`). Якщо задача імпорту впала 3 рази поспіль, адміну приходить повідомлення в Telegram.

## Етапи та правила для агента

П'ять етапів. Кожен закінчується робочим розгортанням, яким сім'я може користуватися. Після етапу 2 продукт уже розв'язує основну проблему. Наступний етап починати лише після того, як виконані всі критерії попереднього.

1. **Фундамент.** Інвентаризація VPS, репозиторій, Dockerfile і compose, база та міграції зі seed-довідниками, сабдомен із TLS, вебхук, білий список і `/invite`.
   - **Готово, коли:** `/start` відповідає членам сім'ї і відмовляє чужим, `/healthz` повертає 200, бекап створено й перевірено відновленням у тестову базу.
2. **Швидке додавання й «Що приготувати?» у чаті.** Текстовий і голосовий ввід через LLM, картка-чернетка з усіма кнопками, масове додавання списком, `/what` з алгоритмом зважування, `/find`, «Готую!», `cook_log`, `/export`.
   - **Готово, коли:** повідомлення «борщ, деруни, олів'є — вміє мама» дає 3 страви з правильними кухарем і тегами не більш ніж за 2 дотики; `/what` не повторює страву, приготовану вчора.
3. **Імпорт за посиланням.** Черга `import_jobs` і worker, `WebRecipeImporter`, `VideoImporter`, `LinkOnlyImporter`, фото з прев'ю, дедуплікація за URL, фото-ввід через vision.
   - **Готово, коли:** тестовий набір із 10 посилань (4 українські кулінарні сайти, 3 TikTok, 2 Instagram Reels, 1 YouTube Shorts) дає чернетки з назвою і фото щонайменше для 8 з 10; жодне посилання не губиться; повторне посилання відкриває наявну страву. Набір посилань дасть власник.
4. **Mini App.** Головна з великою кнопкою, каталог із фільтрами, картка та редагування страви, завантаження фото, кнопка меню бота.
   - **Готово, коли:** від відкриття Mini App до трьох пропозицій минає не більше 2 с на телефоні; фільтри комбінуються і зберігаються в URL; світла й темна теми коректні на Android і в Telegram Desktop.
5. **Розширення (за потреби).** Вхід у браузері через Telegram Login Widget, `MealieImporter` як резерв, налаштування тегів у Mini App, PWA-маніфест для домашнього екрана Android замість окремого застосунку.

**Правила для агента**

- Не змінювати існуючі сервіси, бази, конфіги nginx та ботів. Новий сайт оформлювати окремим файлом конфігурації. Перед `reload` проходити перевірку `nginx -t`.
- Секрети зберігати лише в `.env` і `secrets/`: обидва в `.gitignore`. Токени в логи не потрапляють.
- Тести `pytest` покривають: перевірку `initData`, алгоритм suggest (фіксований `rng`), мапінг `RawExtract` → `DishDraft` на збережених HTML/JSON-фікстурах (без мережі) і фільтри `GET /api/dishes`. LLM у тестах замінюється фейком.
- Тексти інтерфейсу та бота — українською, зібрані в одному файлі `texts.py`.
- `README.md` містить локальний запуск, деплой (`git pull && docker compose up -d --build`), відновлення з бекапу та оновлення yt-dlp.
- Після кожного етапу агент звітує: що зроблено, як перевірити в Telegram, наскільки виконано критерії готовності та які рішення він ухвалив самостійно.
- Зупинитися й запитати власника потрібно, коли треба створити DNS-запис або бота, отримати API-ключ чи cookies, а також для будь-якої дії, що зачіпає не лише цей проєкт.

## Джерела

- [Mealie v3.13 — імпорт з YouTube, TikTok і Instagram через OpenAI](https://newreleases.io/project/github/mealie-recipes/mealie/release/v3.13.0)
- [Mealie — масовий імпорт через `/api/recipes/create/url`](https://docs.mealie.io/documentation/community-guide/bulk-url-import)
- [Приклад пайплайну yt-dlp → LLM для рецептів з TikTok](https://xeiaso.net/notes/2024/cooktok)
