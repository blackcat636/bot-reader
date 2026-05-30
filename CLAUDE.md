# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A "reader mode" service that fetches an article URL, strips ads/banners/popups/comments,
saves a clean copy, and exports it as PDF, Markdown, HTML, or EPUB — or reads it **inside
Telegram** (text messages in-chat, or a telegra.ph Instant View page). Two clients share one
backend and one article history:

- **Telegram bot** (`bot/`) — send a URL, pick a format, get the file (or read it in Telegram).
- **Chrome extension** (`extension/`, MV3) — save the current tab from the browser.

A Telegram account and a browser can be **merged** (via `/link`) so both see the same history.

## Components

| Path          | Role                                                                    |
|---------------|-------------------------------------------------------------------------|
| `api/`        | FastAPI backend — the only place that fetches, extracts, converts, stores |
| `bot/`        | python-telegram-bot long-polling client; talks to the API over HTTP     |
| `renderer/`   | Headless-browser microservice (Playwright); renders JS/Cloudflare pages, returns raw HTML over HTTP |
| `extension/`  | Chrome MV3 extension (popup + background) calling the same API           |
| `i18n/`       | `uk.json` / `en.json` translation tables, shared by API and bot          |

### `api/` modules

- `main.py` — FastAPI app + all endpoints. `lifespan` runs `init_db()`, the link-code cleanup loop,
  and `pending_extracts.cleanup_loop` (sweeps Futures older than `PARSER_WAIT_TIMEOUT_SEC*2`).
  Endpoints: `/extract`, `/internal/parser-callback` (browser-agent callback, Bearer-auth via
  `PARSER_CALLBACK_SECRET` — only when set), `/articles/{id}` (GET/DELETE),
  `/articles/{id}/download`, `/articles/{id}/read` (Telegram-HTML chunks for in-chat reading),
  `/articles/{id}/telegraph` (publish to telegra.ph → Instant View URL), `/history`, `/search`,
  `/share/*`, `/link/*`, `/users/{id}` (lang), `/admin/*`. The `/admin/*` group (stats, users list, ban/unban, failed-URL log, plus per-user
  article browse/download/delete) is gated by `_require_admin(admin_id)` — `admin_id` must be in
  `ADMIN_IDS` (env, shared with the bot) or 403. `/extract` also rejects banned users (403
  `err_banned`), records every extraction failure via `log_failed_url`, and `touch_user`s
  `last_active_at`/`username` on each call. The download Response is built by the shared
  `_render_download(article, format, lang)` helper, used by both `/articles/{id}/download` (group
  auth) and `/admin/articles/{id}/download` (admin auth) — they differ only in how the article is
  fetched (`get_article` vs `get_article_any`).
- `extractor.py` — `fetch_and_extract`: **cascade**. Fast path is `httpx` GET (desktop Chrome UA,
  30 s, redirects), rejects non-HTML, runs `_extract_from_html` (readability + the <200-char
  `ExtractError("no_content")` check). On `no_content`, HTTP 403/429/503, or timeout it falls back
  via `_fallback_or_raise`. Two backends (env `EXTRACT_BACKEND`):
  - `legacy` (default) — POST to `RENDERER_URL`, the local `renderer` service.
  - `browser_agent` — POST to external `browser-agent` via `api/browser_agent.py`
    (`fetch_html_via_get_page`). If BA itself is unreachable (`BrowserAgentUnavailable`) the code
    **silently sweeps to legacy `renderer`** as a safety net; BA's own errors (`blocked`,
    `no_content`, `timeout`) are terminal.
  In either mode the returned HTML is re-run through `_extract_from_html`. `not_html` stays
  terminal. If the chosen fallback is fully unreachable the original error is re-raised.
  `_extract_from_html` (sync, CPU-bound readability) is always invoked via `asyncio.to_thread`.
- `errors.py` — `ExtractError` lives here (was in `extractor.py`). Re-exported by `extractor` for
  back-compat; `browser_agent.py` imports from `errors` directly to avoid a circular import.
- `browser_agent.py` — client for external **browser-agent** (`generic/get_page`). `POST /api/run`
  with `callback_url=PARSER_CALLBACK_BASE_URL/internal/parser-callback`,
  `callback_headers={Authorization: Bearer PARSER_CALLBACK_SECRET}`, `callback_context={extractId}`.
  Registers a Future in `pending_extracts` and races it against poll of `GET /api/tasks/{id}` —
  whichever resolves first wins (poll-fallback for lost callbacks). `BrowserAgentUnavailable` is
  the **only** exception that lets `extractor.py` sweep to legacy renderer; everything else
  (blocked/no_content/timeout) is terminal.
- `pending_extracts.py` — in-memory `{extract_id → asyncio.Future}` with `register/resolve/reject/
  discard`. `resolve`/`reject` are idempotent (callback + poll may both fire). `cleanup_loop` runs
  in `lifespan` and expires Futures older than `PARSER_WAIT_TIMEOUT_SEC*2` so a missed callback +
  api restart doesn't leak Futures forever.
- `converter.py` — `generate_file` renders the stored HTML into the four formats. It is a plain
  **sync** function (weasyprint/html2text/ebooklib are all CPU-bound) — `main.py` calls it via
  `asyncio.to_thread` so it never blocks the event loop. PDF/HTML use the module-level `READER_CSS`
  (Georgia serif, A4, page numbers). `safe_filename` keeps Unicode letters (`\w` + `re.UNICODE`).
  `MEDIA_TYPES` / `EXTENSIONS` enumerate supported formats.
- `telegram_view.py` — turns the stored `content_html` into the two in-Telegram reading formats.
  Both are **sync, CPU-bound** (lxml parse) → `main.py` calls them via `asyncio.to_thread`, same as
  `converter`. `html_to_chunks` maps the HTML to Telegram's tiny allowed inline subset
  (`b/i/u/s/a/code` + `blockquote`/`pre`), drops images/tables/figures (text-only chat mode), and
  packs paragraph "blocks" into the fewest messages ≤3800 chars; oversized blocks are stripped of
  tags and re-escaped word-by-word so a tag/entity is never split across a message.
  `html_to_telegraph_nodes` maps to Telegraph's node array (h1/h2→h3, h5/h6→h4, unknown tags
  unwrapped, `a`/`img` URLs resolved absolute via the article URL).
- `telegraph.py` — minimal async telegra.ph client. `create_page(title, nodes)` lazily creates an
  account on first publish; the token lives **in process memory only** (`_token` + an asyncio lock),
  losing it on restart is fine — already-published pages stay reachable. Optional `TELEGRAPH_TOKEN`
  env to pin one. Raises `TelegraphError` on API `ok:false`.
- `db.py` — `aiosqlite`, file at `DB_PATH` (default `/app/data/articles.db`). `init_db` creates
  tables and runs idempotent `_migrate()` (adds `telegraph_url` to old `articles`; `group_id`,
  `lang`, `username`, `is_banned`, `last_active_at` to old `users` rows). `set_article_telegraph_url`
  caches the published Instant View URL so re-tapping ⚡ doesn't create a new telegra.ph page. Admin helpers: `list_users`/`count_users` (with per-user
  `article_count`), `set_user_banned`/`is_user_banned`, `touch_user`, `get_admin_stats`,
  `log_failed_url`/`list_failed_urls`/`count_failed_urls`, and `get_article_any`/`delete_article_any`
  (by id, **no group check** — admin-only; the non-admin `get_article`/`delete_article` always scope
  to the caller's group). Admins browse a user's articles via `get_user_history(target_user_id)`,
  which already joins through that user's group.
- `i18n.py` — loads `i18n/*.json`; `t(lang, key, **kwargs)` formats a string, `normalize` maps a
  locale code to `uk`/`en`. **Note:** `lang` is `t`'s first positional arg, so a translation
  placeholder must never be named `{lang}` — it would collide as a kwarg. The user-card string
  uses `{ulang}` for this reason.

### `renderer/` modules

- `main.py` — small FastAPI app on **camoufox** (anti-detect Firefox), not vanilla Playwright
  chromium: Cloudflare managed Turnstile fingerprints and blocks headless chromium even under Xvfb.
  `POST /render {url}` launches a **fresh camoufox per attempt** (a long-lived Firefox is unstable),
  navigates, and on a challenge page clicks the Turnstile checkbox (reached via the nested
  `challenges.cloudflare.com` frame's `frame_element` bounding box), then returns `{html, status,
  final_url, title}`. `GET /health` for compose. Knows nothing about readability/DB.
- `solver.py` — pluggable, **disabled by default**. `solve_if_present(page)` is a no-op unless both
  `CAPTCHA_PROVIDER` and `CAPTCHA_API_KEY` are set. Skeleton targets 2captcha (detect sitekey →
  submit → poll token → inject). Experimental; paid + ToS caveats (see README).

  Hard-won operational notes (all required for camoufox to work as a service):
  - uvicorn must run with `--loop asyncio` — camoufox/Playwright's subprocess transport corrupts
    under uvicorn's default **uvloop** (browser connection dies mid-render).
  - the container needs `init: true` — crashed Firefox children leave **zombies** that pile up
    (uvicorn as PID 1 doesn't reap them) and destabilise the service.
  - `render()` retries (`RENDER_ATTEMPTS`) both on a driver crash **and** when the page is still a
    challenge after the budget — camoufox randomises the fingerprint per launch, so a fresh launch
    is another shot at passing Turnstile.
  - A Playwright-Firefox driver bug used to crash the **whole Node driver process** on challenge
    pages: an uncaught page JS error arrives as a `pageError` with no `location`, and the driver's
    `addPageError` reads `pageError.location.url` → uncaught `TypeError` → "Connection closed while
    reading from the driver" for every subsequent call. The Python `page.on("pageerror")` listener
    does **not** help (the crash is in Node, before the event reaches Python). `renderer/Dockerfile`
    patches `coreBundle.js` after `pip install`, rewriting those reads to safe defaults
    (`pageError.location?.url ?? ""`, `… ?? 0` for line/column — the same defaults the driver uses
    internally). Plain `?.` is **not** enough: `undefined` then fails the protocol-event validator
    (`tString: location.url expected string`), just moving the crash downstream — the `?? ""`/`?? 0`
    defaults are required. The build asserts the patch applied (`test "$found" = 1`). If you bump
    playwright/camoufox, re-check the patch still matches. With it in place, managed-Turnstile
    reliability is governed by the fingerprint lottery + retries, not by hard driver crashes.

- `groups` — a shared history bucket. Every user belongs to exactly one group.
- `users` — `user_id` (Telegram id or browser uuid), `type` (`telegram`/`browser`), `group_id`,
  `lang`, `username` (display only, set by the bot on `/extract`), `is_banned`, `last_active_at`.
- `articles` — `user_id`, `url`, `title`, `content_html`, `telegraph_url` (cached Instant View URL,
  nullable). Queries join through the user's group, so all members of a group see all articles.
  Deduped by URL within a group.
- `failed_urls` — `user_id`, `url`, `error`, `created_at`. Append-only log of extraction failures,
  written by `/extract` so admins can triage broken sites via `/failed`. Not auto-purged.
- `link_codes` — 6-char, 10-min codes for merging two groups (`/link`).
- `share_codes` — 8-char one-time codes for copying a single article to another user (`/share`).

## Commands

```bash
# Full stack via Docker (preferred — handles weasyprint + playwright system deps)
docker compose up -d --build            # api + bot + renderer
docker compose up -d --build bot        # rebuild just the bot
docker compose up -d --build renderer   # rebuild just the renderer (Playwright image)
docker compose --profile tunnel up -d   # also start cloudflared
docker compose logs -f api bot renderer
docker compose down

# Local API (needs libcairo2, libpango-1.0-0, libpangocairo-1.0-0,
# libgdk-pixbuf-2.0-0, fonts-liberation, fonts-dejavu-core on the host)
pip install -r requirements.txt
uvicorn api.main:app --reload      # API on :8000
python -m bot.bot                  # bot (reads API_URL, default http://api:8000)
```

`.env` needs `BOT_TOKEN` (from @BotFather). Optional: `ADMIN_IDS` (comma-separated Telegram ids;
both `api` and `bot` read it from `.env` via `env_file`, so it needs no `environment:` entry),
`DB_PATH`, `API_DOMAIN` (nginx-proxy), `CLOUDFLARE_TUNNEL_TOKEN` (tunnel profile),
`CAPTCHA_PROVIDER` + `CAPTCHA_API_KEY` (renderer's captcha solver, off unless both set),
`TELEGRAPH_TOKEN` (pin a telegra.ph account for Instant View; otherwise one is auto-created in
memory). `EXTRACT_BACKEND=browser_agent` swaps the fallback to external browser-agent and needs
`BROWSER_AGENT_URL` + `BROWSER_AGENT_API_KEY` + `PARSER_CALLBACK_BASE_URL` (publicly-reachable URL
of this api, e.g. cloudflared) + `PARSER_CALLBACK_SECRET`; tunables `PARSER_WAIT_TIMEOUT_SEC`
(default 110, must be < bot's 130s), `PARSER_POLL_INTERVAL_SEC`, `PARSER_MAX_BYTES`,
`PARSER_FORCE_TIER`. `API_URL` (bot→api) and `RENDERER_URL` (api→renderer) are injected by
compose. No test suite, no linter.

## Compose services

- `api` — `uvicorn api.main:app`, exposes `8000`, mounts `./data` for the SQLite db; depends on
  `renderer`, reaches it at `http://renderer:8001`.
- `bot` — `python -m bot.bot`, depends on `api`, reaches it at `http://api:8000`.
- `renderer` — `uvicorn renderer.main:app --loop asyncio`, exposes `8001`. Built from
  `renderer/Dockerfile` (own image on the Playwright base, **not** the shared `Dockerfile`; the
  Playwright base only supplies system libs + Xvfb — the actual browser is camoufox's Firefox,
  fetched at build via `python -m camoufox fetch`). Needs `shm_size: 1gb` and `init: true`.
- `cloudflared` — optional public tunnel, only with `--profile tunnel`.

## Things to be aware of

- **The bot holds no logic of its own** — it fetches/extracts/converts nothing. Every action is an
  HTTP call to the API. New features usually mean a new endpoint in `api/` plus a handler in `bot/`.
- **Admin auth is two-layered and env-driven.** `ADMIN_IDS` is read by *both* sides: the bot uses
  `is_admin()` to hide the `/admin`, `/users`, `/stats`, `/failed` commands from non-admins, and
  the API independently re-checks `admin_id ∈ ADMIN_IDS` on every `/admin/*` call (the bot's check
  is UX, the API's is the real gate). There's no `is_admin` DB column — admin status lives only in
  env, so granting/revoking admin is an env edit + restart, never a DB write.
- **Concurrency** is end-to-end: the bot runs `Application` with `concurrent_updates(True)` so a
  long `/extract` from one user never serialises the dispatcher for everyone else; on the API side
  the CPU-bound work (readability extraction, weasyprint/epub generation) is pushed off the event
  loop with `asyncio.to_thread`. Both halves are required — fixing only one still freezes the bot.
  Note `concurrent_updates(True)` removes PTB's implicit per-update serialisation, so any new
  shared mutable state between handlers must be guarded (per-user `context.user_data` is fine).
- **`Content-Disposition`** carries both `filename="<ascii>"` and `filename*=UTF-8''<encoded>`. The
  ASCII copy strips Cyrillic; clients must prefer `filename*` (the bot does, `bot/bot.py`).
- **Two parser backends, env-switched** (`EXTRACT_BACKEND`): `legacy` keeps today's path (httpx →
  local `renderer`). `browser_agent` swaps the fallback step for an external `browser-agent`
  service (`generic/get_page`, async `POST /api/run` + callback to `/internal/parser-callback`
  with poll fallback). If BA is unreachable, the code silently sweeps to `renderer`, so prod stays
  resilient when BA flaps. Don't remove `depends_on: renderer` even on `browser_agent` mode — it's
  the safety net. The fast `httpx` path is identical in both modes.
- **Fetch is a cascade** (`api/extractor.py`): httpx first, then the configured browser backend on
  `no_content`/`blocked`/403·429·503/`RequestError`. `_looks_blocked` detects a Cloudflare
  interstitial (markers `cf_chl_opt`, `cdn-cgi/challenge-platform` — deliberately *not*
  `challenges.cloudflare.com`, which also appears on legit pages with a Turnstile widget) so the
  fast path never **saves the challenge page as the article**. If the browser also returns a
  challenge → `ExtractError("blocked")` → `err_blocked` (clean message, no junk saved). If
  `renderer` is down the fast-path error surfaces unchanged — degrades silently, never hard-fails.
- `renderer` has its **own** `renderer/Dockerfile` (Playwright base image). The shared `Dockerfile`
  copies only `api/`, `bot/`, `i18n/` — a new top-level module for api/bot still means updating it,
  but renderer files belong to the renderer image instead.
- The captcha solver (`renderer/solver.py`) is off unless `CAPTCHA_PROVIDER`+`CAPTCHA_API_KEY` are
  set; it's experimental and paid/ToS-sensitive — keep it opt-in.
- `READER_CSS` is parsed once at import time, so style edits need an API restart.
- The bot uses long polling (`run_polling(drop_pending_updates=True)`), not webhooks.
- The SQLite db lives in the `./data` bind mount, so it survives `docker compose down`/rebuilds;
  only deleting the host folder loses it. `.env` and `data/` are gitignored.
- User-facing strings live in `i18n/*.json` (keys resolved via `t(...)`), never hard-coded.
