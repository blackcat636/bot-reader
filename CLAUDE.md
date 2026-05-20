# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Telegram bot that accepts an article URL and replies with a clean PDF rendered in "reader mode" — no ads, banners, popups, or comments. The entire implementation lives in a single file: `bot.py`.

## Commands

```bash
# Run via Docker (preferred — handles weasyprint system deps)
docker compose up -d --build
docker compose logs -f
docker compose restart
docker compose down

# Run locally (requires libcairo2, libpango-1.0-0, libpangocairo-1.0-0,
# libgdk-pixbuf-2.0-0, fonts-liberation, fonts-dejavu-core on the host)
pip install -r requirements.txt
python bot.py
```

`BOT_TOKEN` (from @BotFather) must be set in `.env`. No test suite, no linter configured.

## Architecture

Single-file async bot (`bot.py`). Pipeline for a user-submitted URL:

1. `is_valid_url` — sanity check (`http`/`https` + netloc).
2. `httpx.AsyncClient` fetches the page with a desktop Chrome UA, 30 s timeout, redirects followed. Non-HTML content types are rejected early.
3. `readability.Document` (Mozilla Readability port) extracts `title()` and `summary(html_partial=True)`. If the stripped text is under 200 chars, the bot returns a "paywall / could not extract" message.
4. Extracted HTML is wrapped in a minimal document template that prepends `<h1>{title}</h1>` and a `.source` line linking back to the original URL.
5. `weasyprint.HTML(...).write_pdf(...)` renders with the module-level `READER_CSS` (Georgia serif, A4, page numbers in footer, table/blockquote/code styling). `base_url=text` is set so relative image URLs resolve.
6. PDF is written to a `tempfile.NamedTemporaryFile`, sent via `reply_document` with filename derived from `safe_filename(title)`, then unlinked in `finally`.

Status messages (`⏳`, `📖`, `🖨`) are edited in place on a single Telegram message across pipeline stages, then deleted before the PDF is sent. Specific `httpx` exception classes (`TimeoutException`, `HTTPStatusError`, `RequestError`) map to distinct user-facing error messages; anything else is logged with `logger.exception` and surfaces as a generic error.

## Things to be aware of

- JS-rendered SPAs without SSR will return empty content — readability runs on the raw HTML response, no browser.
- `READER_CSS` is parsed once at import time (module-level `CSS(...)`), so style edits require a restart.
- `bot.py` is the only application file copied into the Docker image; adding new modules requires updating the `Dockerfile`.
- The bot uses long polling (`run_polling(drop_pending_updates=True)`), not webhooks.
