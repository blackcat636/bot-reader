# Telegram Reader Bot + Chrome Extension

Сервіс для читання статей без реклами, банерів і коментарів. Використовує алгоритм Mozilla Readability і повертає чистий файл у форматі PDF, Markdown, HTML або EPUB.

Складається з двох клієнтів на спільному FastAPI бекенді:
- **Telegram бот** — надішли URL, отримай файл
- **Chrome розширення** — кнопка прямо на сторінці

---

## Структура проекту

```
reader-bot/
├── api/                    # FastAPI бекенд
│   ├── main.py             # Endpoints
│   ├── extractor.py        # fetch + Mozilla Readability
│   ├── converter.py        # PDF / MD / HTML / EPUB
│   └── db.py               # SQLite (aiosqlite)
├── bot/
│   └── bot.py              # Telegram бот (HTTP клієнт до API)
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

Запускається два контейнери: `api` (порт 8000, тільки всередині Docker-мережі) і `bot`.

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
| `DB_PATH` | — | Шлях до SQLite (за замовчуванням `/app/data/articles.db`) |
| `API_DOMAIN` | — | Домен для nginx-proxy + Let's Encrypt |
| `CLOUDFLARE_TUNNEL_TOKEN` | — | Токен Cloudflare Tunnel |

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
docker compose restart api                    # перезапустити API
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
| `weasyprint` | HTML → PDF |
| `html2text` | HTML → Markdown |
| `ebooklib` | Генерація EPUB |
| `aiosqlite` | Асинхронна робота з SQLite |

### Системні залежності (у Dockerfile)

`libcairo2`, `libpango`, `libgdk-pixbuf` — рендеринг для weasyprint.  
`fonts-liberation`, `fonts-dejavu-core` — шрифти для PDF.
