# План: Спільний FastAPI бекенд для Telegram бота і Chrome розширення

## Context

Зараз вся логіка зосереджена в `bot.py` (fetch → readability → конвертація → SQLite). Ціль — винести бізнес-логіку в окремий FastAPI сервіс, щоб:
- Telegram бот став тонким клієнтом (HTTP виклики до API)
- Chrome розширення могло викликати той самий API безпосередньо з браузера
- Користувачі бота і розширення — окремі сутності (ідентифікуються по `user_id`)

---

## Структура проекту

```
reader-bot/
├── api/
│   ├── main.py          # FastAPI routes
│   ├── extractor.py     # fetch + readability (з bot.py)
│   ├── converter.py     # PDF/MD/HTML/EPUB (з bot.py)
│   └── db.py            # SQLite ops (з bot.py)
├── bot/
│   └── bot.py           # Telegram бот → httpx виклики до API
├── extension/
│   ├── manifest.json    # Manifest V3
│   ├── popup.html
│   └── popup.js
├── Dockerfile           # shared image (weasyprint deps)
├── docker-compose.yml
└── requirements.txt
```

---

## API

### Endpoints

```
POST /extract
  Body: { url: str, user_id: str }
  → fetch URL, readability, save to DB
  → 200: { id, title, url, created_at }
  → 400: { detail: "..." }  (paywall, non-html, etc.)

GET /articles/{id}/download?format=pdf|md|html|epub&user_id=xxx
  → генерує файл, повертає StreamingResponse
  → Content-Disposition: attachment; filename="..."

GET /history?user_id=xxx
  → [{ id, title, url, created_at }, ...]
```

### Auth

Без авторизації на першому етапі — `user_id` передається як параметр:
- Telegram бот: Telegram `user.id` (integer як string)
- Chrome розширення: UUID, згенерований при першій установці, збережений у `chrome.storage.local`

---

## Зміни у файлах

### `api/extractor.py`
Перенести з `bot.py`:
- `FETCH_HEADERS`
- `is_valid_url()`
- `fetch_and_extract(url)` → async, повертає `{ title, content_html }` або кидає виняток

### `api/converter.py`
Перенести з `bot.py`:
- `READER_CSS`, `READER_CSS_STRING`
- `build_page_html()`
- `safe_filename()`
- `generate_file(title, url, content_html, fmt)` → async, повертає `(bytes, filename)`

### `api/db.py`
Перенести з `bot.py`:
- `init_db()`, `save_article()`, `get_article()`, `get_user_history()`
- `DB_PATH` береться з env

### `api/main.py`
FastAPI app:
```python
@app.post("/extract")
@app.get("/articles/{article_id}/download")
@app.get("/history")
```
CORS: дозволити всі origins (потрібно для розширення).

### `bot/bot.py`
Замінити прямі виклики функцій на httpx запити до API:
- `httpx.AsyncClient` вже є в залежностях
- `API_URL` з env (для Docker: `http://api:8000`)
- Логіка Telegram handlers залишається, тільки джерело даних — API

### `docker-compose.yml`
```yaml
services:
  api:
    build: .
    command: uvicorn api.main:app --host 0.0.0.0 --port 8000
    volumes: [./data:/app/data]
    ports: ["8000:8000"]

  bot:
    build: .
    command: python -m bot.bot
    env_file: .env
    environment:
      API_URL: http://api:8000
    depends_on: [api]
    volumes: [./data:/app/data]
```

### `extension/manifest.json` (Manifest V3)
```json
{
  "manifest_version": 3,
  "permissions": ["activeTab", "storage"],
  "host_permissions": ["http://localhost:8000/*"],
  "action": { "default_popup": "popup.html" }
}
```

### `extension/popup.js`
1. `chrome.storage.local.get("userId")` — якщо нема, генерує UUID і зберігає
2. `chrome.tabs.query` → отримує поточний URL
3. Показує 4 кнопки формату
4. При кліку: `POST /extract` → `GET /articles/{id}/download?format=...` → `window.open(blobUrl)`

---

## Порядок реалізації

1. Створити `api/db.py`, `api/extractor.py`, `api/converter.py` (перенести код)
2. Створити `api/main.py` з трьома endpoints
3. Оновити `docker-compose.yml` (два сервіси)
4. Рефакторити `bot/bot.py` → HTTP виклики
5. Створити `extension/` (manifest, popup)
6. Перевірити: запустити `docker compose up`, протестувати API через curl, потім бот, потім розширення

---

## Перевірка

```bash
# API
curl -X POST http://localhost:8000/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://habr.com/...", "user_id": "test"}'

curl "http://localhost:8000/articles/1/download?format=pdf&user_id=test" -o test.pdf

curl "http://localhost:8000/history?user_id=test"

# Бот — надіслати посилання в Telegram
# Розширення — встановити як unpacked, відкрити будь-яку статтю
```
