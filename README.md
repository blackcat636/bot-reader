# Telegram Reader Bot + Chrome Extension

Сервіс для читання статей без реклами, банерів і коментарів. Використовує алгоритм Mozilla Readability і повертає чистий файл у форматі PDF, Markdown, HTML або EPUB.

Статтю також можна **читати прямо в Telegram**, не завантажуючи файл:
- 📖 **Читати тут** — текст приходить повідомленнями просто в чат (приватно, нічого назовні).
- ⚡ **Instant View** — стаття публікується на telegra.ph і відкривається нативною читалкою Telegram (краще форматування й картинки, але контент лежить на зовнішньому сервісі).

Складається з двох клієнтів на спільному FastAPI бекенді:
- **Telegram бот** — надішли URL, отримай файл
- **Chrome розширення** — кнопка прямо на сторінці

Завантаження сторінок **каскадне**: спершу швидкий HTTP-запит, а якщо сайт віддає
порожнечу (JS-рендерена SPA) або блокує бота (Cloudflare 403/«Just a moment…») —
сторінка догружається справжнім headless-браузером (сервіс `renderer`).

---

## Структура проекту

```
reader-bot/
├── api/                    # FastAPI бекенд
│   ├── main.py             # Endpoints
│   ├── extractor.py        # fetch + Mozilla Readability
│   ├── converter.py        # PDF / MD / HTML / EPUB
│   ├── telegram_view.py    # content_html → Telegram-HTML чанки / Telegraph-вузли
│   ├── telegraph.py        # async-клієнт telegra.ph (Instant View)
│   └── db.py               # SQLite (aiosqlite)
├── bot/
│   └── bot.py              # Telegram бот (HTTP клієнт до API)
├── renderer/               # Headless-браузер (Playwright) для JS/Cloudflare
│   ├── main.py             # POST /render → відрендерений HTML
│   ├── solver.py           # опційний гачок під капч-солвер (вимкнений)
│   └── Dockerfile          # власний образ на базі playwright/python
├── extension/
│   ├── manifest.json       # Chrome Extension Manifest V3
│   ├── popup.html
│   └── popup.js
├── data/                   # SQLite БД (монтується як volume)
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Запуск

### 1. Налаштування

```bash
cp .env.example .env
# Відредагувати .env — обов'язково вписати BOT_TOKEN
```

### 2. Стандартний запуск (без тунелю)

```bash
docker compose up -d --build
```

Запускається три контейнери: `api` (порт 8000, тільки всередині Docker-мережі), `bot`
і `renderer` (headless-браузер, порт 8001 у Docker-мережі). Перша збірка `renderer`
довша — тягне образ Playwright з chromium (~1.5 ГБ), зате образи `api`/`bot` лишаються легкими.

### 3. З Cloudflare Tunnel

Якщо немає публічного IP або потрібен HTTPS без налаштування nginx:

1. Створити тунель у [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → Networks → Tunnels
2. Додати маршрут: `http://api:8000`
3. Скопіювати токен тунелю в `.env`:

```env
CLOUDFLARE_TUNNEL_TOKEN=your_token_here
```

4. Запустити з профілем `tunnel`:

```bash
docker compose --profile tunnel up -d --build
```

### 4. З власним доменом через nginx-proxy

Якщо на сервері вже працює [jwilder/nginx-proxy](https://github.com/nginx-proxy/nginx-proxy):

```env
API_DOMAIN=reader.your-domain.com
```

`VIRTUAL_HOST` і `LETSENCRYPT_HOST` підхоплюються автоматично.

---

## Конфігурація `.env`

| Змінна | Обов'язкова | Опис |
|---|---|---|
| `BOT_TOKEN` | ✅ | Токен від [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | — | Telegram id адміністраторів через кому. Вмикає команди `/admin`, `/users`, `/stats`, `/failed`. Свій id — через [@userinfobot](https://t.me/userinfobot) |
| `DB_PATH` | — | Шлях до SQLite (за замовчуванням `/app/data/articles.db`) |
| `API_DOMAIN` | — | Домен для nginx-proxy + Let's Encrypt |
| `CLOUDFLARE_TUNNEL_TOKEN` | — | Токен Cloudflare Tunnel |
| `RENDERER_URL` | — | URL рендерера (compose задає `http://renderer:8001`; порожнє вимикає браузерний фолбек) |
| `CAPTCHA_PROVIDER` | — | Провайдер капч-солвера (`2captcha`). Вимкнено, якщо не задано |
| `CAPTCHA_API_KEY` | — | Ключ API солвера. Вмикає гачок розвʼязання капч (див. нижче) |

---

## JS-сайти, Cloudflare і капчі

Якщо прямий HTTP-запит повертає порожнечу, заглушку анти-бота, 403/429/503 або обрив/таймаут,
`api` звертається до сервісу `renderer` — це **camoufox** (анти-детект Firefox), який:

- виконує JavaScript, тож працюють SPA без SSR;
- проходить **JS/Cloudflare-челенджі**, включно з клікабельним Turnstile («Трохи зачекайте…»),
  безкоштовно, без зовнішніх сервісів.

**Надійність на сайтах із Cloudflare managed Turnstile (напр. dou.ua) — часткова (~½).**
camoufox рандомізує фінгерпринт на кожен запуск, тож `renderer` повторює спробу зі свіжим
браузером (`RENDER_ATTEMPTS`); частина спроб усе одно впирається в баг драйвера playwright-Firefox.
Звичайні сайти, SPA та мʼякі челенджі — стабільні. Якщо челендж не пройдено, бот повертає
зрозуміле «сайт захищено», а не сміття. Для гарантованого обходу таких сайтів — платний
солвер (нижче).

### Капч-солвер (експериментально, вимкнено за замовчуванням)

⚠️ Інтерактивні капчі (reCAPTCHA з картинками, hCaptcha) алгоритмічно не вирішуються.
Гачок `renderer/solver.py` може делегувати їх **платному зовнішньому сервісу** (наразі
скелет під [2captcha](https://2captcha.com) — людська ферма). Це **коштує гроші** за
кожен розвʼязок і часто **порушує Terms of Service** сайту. Вмикати лише за наявності
чіткого дозволу на конкретний ресурс:

```env
CAPTCHA_PROVIDER=2captcha
CAPTCHA_API_KEY=your_key
```

Без цих змінних солвер — no-op: сайт із капчею просто коректно завершується помилкою
«не вдалося виділити текст».

---

## API

Доступний всередині Docker-мережі на `http://api:8000`. При підключеному домені або тунелі — публічно.

| Метод | Endpoint | Опис |
|---|---|---|
| `POST` | `/extract` | Завантажити статтю, зберегти в БД (дедуп по URL у межах групи) |
| `GET` | `/articles/{id}?user_id=` | Інфо про статтю |
| `DELETE` | `/articles/{id}?user_id=` | Видалити статтю |
| `GET` | `/articles/{id}/download?format=pdf\|md\|html\|epub&user_id=` | Скачати файл |
| `GET` | `/history?user_id=&limit=&offset=` | Збережені статті (з пагінацією) |
| `GET` | `/search?user_id=&q=` | Пошук по заголовку |
| `POST` | `/share/generate`, `/share/claim`, `DELETE /share/{code}` | Поділитися статтею (одноразовий 8-символьний код) |
| `POST` | `/link/generate`, `/link/confirm`, `GET /link/preview` | Об'єднати акаунти (6-символьний код, 10 хв) |
| `GET` | `/users/{id}`, `PATCH /users/{id}/lang` | Мова користувача |
| `GET` | `/admin/stats?admin_id=` | Зведена статистика (тільки адмін) |
| `GET` | `/admin/users?admin_id=&limit=&offset=` | Список користувачів з кількістю статей (тільки адмін) |
| `POST` | `/admin/users/{id}/ban`, `/admin/users/{id}/unban` `?admin_id=` | Блокування / розблокування (тільки адмін) |
| `GET` | `/admin/failed?admin_id=&limit=&offset=` | Лог невдалих посилань (тільки адмін) |
| `GET` | `/admin/users/{id}/articles?admin_id=&limit=&offset=` | Статті користувача (тільки адмін) |
| `GET` | `/admin/articles/{id}?admin_id=` | Інфо про статтю без перевірки групи (тільки адмін) |
| `GET` | `/admin/articles/{id}/download?format=&admin_id=` | Скачати статтю користувача (тільки адмін) |
| `DELETE` | `/admin/articles/{id}?admin_id=` | Видалити будь-яку статтю (тільки адмін) |

### Приклад

```bash
# Завантажити статтю
curl -X POST https://reader.your-domain.com/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://habr.com/ru/articles/123/", "user_id": "test"}'
# → {"id": 1, "title": "...", "url": "..."}

# Скачати PDF
curl "https://reader.your-domain.com/articles/1/download?format=pdf&user_id=test" -o article.pdf
```

---

## Команди бота

| Команда / дія | Опис |
|---|---|
| `/start` | Привітання та інструкція |
| `/help` | Повна довідка по всіх можливостях |
| `/history` | Список збережених статей (з пагінацією) для повторної конвертації, шерінгу чи видалення |
| `/find` | Інтерактивний пошук по заголовку (бот запитає слово) |
| `/link` | Об'єднати Telegram-акаунт із Chrome-розширенням (спільна історія) |
| `/settings` | Зміна мови (uk / en) |
| Будь-який текст з URL | Завантажити статтю, обрати формат (PDF / MD / HTML / EPUB) |
| 8-символьний код | Прийняти статтю, якою з тобою поділилися |
| 6-символьний код | Підтвердити об'єднання акаунтів |

### Команди адміністратора

Доступні лише користувачам зі списку `ADMIN_IDS`.

| Команда | Опис |
|---|---|
| `/admin` | Меню адміністратора з переліком команд |
| `/stats` | Зведена статистика: користувачі, статті, активність, заблоковані |
| `/users` | Список користувачів; натисни на користувача → картка з кнопками бан/розбан і 📄 Статті |
| `/failed` | Посилання, які не вдалося завантажити (для розбору проблемних сайтів) |

З картки користувача → **📄 Статті** адмін бачить усі статті цього користувача, може відкрити будь-яку, завантажити в PDF / MD / HTML / EPUB або видалити.

---

## Chrome розширення

Встановлення як unpacked:

1. Відкрити `chrome://extensions`
2. Увімкнути **Developer mode**
3. **Load unpacked** → обрати папку `extension/`
4. У popup розширення вказати **API URL** (наприклад `https://reader.your-domain.com`)

При першому запуску генерується UUID користувача і зберігається в `chrome.storage.local`.

---

## Корисні команди Docker

```bash
docker compose logs -f                        # логи всіх сервісів
docker compose logs -f api                    # логи тільки API
docker compose logs -f renderer               # логи браузерного рендерера
docker compose restart api                    # перезапустити API
docker compose up -d --build renderer         # перезібрати тільки renderer
docker compose --profile tunnel up -d         # запуск з тунелем
docker compose down                           # зупинка
```

---

## Залежності

| Пакет | Призначення |
|---|---|
| `fastapi` + `uvicorn` | HTTP API |
| `python-telegram-bot` | Telegram Bot API |
| `httpx` | Завантаження сторінок |
| `readability-lxml` | Mozilla Readability — виділення тексту |
| `camoufox` | Анти-детект Firefox (сервіс `renderer`) для JS/Cloudflare |
| `weasyprint` | HTML → PDF |
| `html2text` | HTML → Markdown |
| `ebooklib` | Генерація EPUB |
| `aiosqlite` | Асинхронна робота з SQLite |

### Системні залежності (у Dockerfile)

`libcairo2`, `libpango`, `libgdk-pixbuf` — рендеринг для weasyprint.  
`fonts-liberation`, `fonts-dejavu-core` — шрифти для PDF.
