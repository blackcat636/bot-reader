# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A "reader mode" service that fetches an article URL, strips ads/banners/popups/comments,
saves a clean copy, and exports it as PDF, Markdown, HTML, or EPUB. Two clients share one
backend and one article history:

- **Telegram bot** (`bot/`) — send a URL, pick a format, get the file.
- **Chrome extension** (`extension/`, MV3) — save the current tab from the browser.

A Telegram account and a browser can be **merged** (via `/link`) so both see the same history.

## Components

| Path          | Role                                                                    |
|---------------|-------------------------------------------------------------------------|
| `api/`        | FastAPI backend — the only place that fetches, extracts, converts, stores |
| `bot/`        | python-telegram-bot long-polling client; talks to the API over HTTP     |
| `extension/`  | Chrome MV3 extension (popup + background) calling the same API           |
| `i18n/`       | `uk.json` / `en.json` translation tables, shared by API and bot          |

### `api/` modules

- `main.py` — FastAPI app + all endpoints. `lifespan` runs `init_db()` and a background loop
  that purges expired link codes hourly. Endpoints: `/extract`, `/articles/{id}` (GET/DELETE),
  `/articles/{id}/download`, `/history`, `/search`, `/share/*`, `/link/*`, `/users/{id}` (lang).
- `extractor.py` — `fetch_and_extract`: `httpx` GET (desktop Chrome UA, 30 s, redirects),
  rejects non-HTML, runs `readability.Document`; raises `ExtractError("no_content")` if the
  stripped text is under 200 chars (paywall/SPA).
- `converter.py` — `generate_file` renders the stored HTML into the four formats. PDF/HTML use
  the module-level `READER_CSS` (Georgia serif, A4, page numbers). `safe_filename` keeps Unicode
  letters (`\w` + `re.UNICODE`). `MEDIA_TYPES` / `EXTENSIONS` enumerate supported formats.
- `db.py` — `aiosqlite`, file at `DB_PATH` (default `/app/data/articles.db`). `init_db` creates
  tables and runs idempotent `_migrate()` (adds `group_id`, `lang` to old `users` rows).
- `i18n.py` — loads `i18n/*.json`; `t(lang, key, **kwargs)` formats a string, `normalize` maps a
  locale code to `uk`/`en`.

### Data model (`db.py`)

- `groups` — a shared history bucket. Every user belongs to exactly one group.
- `users` — `user_id` (Telegram id or browser uuid), `type` (`telegram`/`browser`), `group_id`, `lang`.
- `articles` — `user_id`, `url`, `title`, `content_html`. Queries join through the user's group, so
  all members of a group see all articles. Deduped by URL within a group.
- `link_codes` — 6-char, 10-min codes for merging two groups (`/link`).
- `share_codes` — 8-char one-time codes for copying a single article to another user (`/share`).

## Commands

```bash
# Full stack via Docker (preferred — handles weasyprint system deps)
docker compose up -d --build            # api + bot
docker compose up -d --build bot        # rebuild just the bot
docker compose --profile tunnel up -d   # also start cloudflared
docker compose logs -f api bot
docker compose down

# Local API (needs libcairo2, libpango-1.0-0, libpangocairo-1.0-0,
# libgdk-pixbuf-2.0-0, fonts-liberation, fonts-dejavu-core on the host)
pip install -r requirements.txt
uvicorn api.main:app --reload      # API on :8000
python -m bot.bot                  # bot (reads API_URL, default http://api:8000)
```

`.env` needs `BOT_TOKEN` (from @BotFather). Optional: `DB_PATH`, `API_DOMAIN` (nginx-proxy),
`CLOUDFLARE_TUNNEL_TOKEN` (tunnel profile). `API_URL` is injected by compose. No test suite,
no linter.

## Compose services

- `api` — `uvicorn api.main:app`, exposes `8000`, mounts `./data` for the SQLite db.
- `bot` — `python -m bot.bot`, depends on `api`, reaches it at `http://api:8000`.
- `cloudflared` — optional public tunnel, only with `--profile tunnel`.

## Things to be aware of

- **The bot holds no logic of its own** — it fetches/extracts/converts nothing. Every action is an
  HTTP call to the API. New features usually mean a new endpoint in `api/` plus a handler in `bot/`.
- **`Content-Disposition`** carries both `filename="<ascii>"` and `filename*=UTF-8''<encoded>`. The
  ASCII copy strips Cyrillic; clients must prefer `filename*` (the bot does, `bot/bot.py`).
- JS-rendered SPAs without SSR return empty content — readability runs on the raw HTML, no browser.
- `READER_CSS` is parsed once at import time, so style edits need an API restart.
- `Dockerfile` copies only `api/`, `bot/`, `i18n/`. Adding a new top-level module means updating it.
- The bot uses long polling (`run_polling(drop_pending_updates=True)`), not webhooks.
- The SQLite db lives in the `./data` bind mount, so it survives `docker compose down`/rebuilds;
  only deleting the host folder loses it. `.env` and `data/` are gitignored.
- User-facing strings live in `i18n/*.json` (keys resolved via `t(...)`), never hard-coded.
