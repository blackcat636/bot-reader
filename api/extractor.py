import asyncio
import logging
import os
import re
from uuid import uuid4

import httpx
from readability import Document

from .browser_agent import BrowserAgentUnavailable, fetch_html_via_get_page
from .errors import ExtractError

logger = logging.getLogger(__name__)

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk,en-US;q=0.9,en;q=0.8",
}

# Окремий браузерний рендерер (сервіс `renderer`). Порожнє значення вимикає фолбек.
RENDERER_URL = os.getenv("RENDERER_URL", "http://renderer:8001").rstrip("/")
RENDER_TIMEOUT = float(os.getenv("RENDER_TIMEOUT", "120"))

# Бекенд браузерного фолбеку: `legacy` (локальний renderer) або `browser_agent`
# (зовнішній browser-agent сервіс). При browser_agent + його недоступності —
# auto-fallback на legacy renderer.
EXTRACT_BACKEND = os.getenv("EXTRACT_BACKEND", "legacy").strip().lower()

# HTTP-статуси, що зазвичай означають bot-блокування — варто перепробувати браузером.
RETRYABLE_STATUSES = {403, 429, 503}

# Маркери сторінки-інтерстиціалу Cloudflare. Мовно-незалежні (службові скрипти,
# а не локалізований текст). Свідомо НЕ беремо challenges.cloudflare.com — він
# лишається й на легітимній сторінці з Turnstile-віджетом у формі.
CHALLENGE_MARKERS = (
    "cf_chl_opt",
    "cdn-cgi/challenge-platform",
)


def _looks_blocked(html: str) -> bool:
    """True, якщо це сторінка анти-бот челенджу, а не реальний контент."""
    low = html.lower()
    return any(m in low for m in CHALLENGE_MARKERS)


def _extract_from_html(html: str) -> dict:
    """Прогнати сирий HTML через readability. Спільне для прямого GET і рендерера."""
    doc = Document(html)
    title = doc.title() or "Стаття"
    content_html = doc.summary(html_partial=True)

    text_len = len(re.sub(r'<[^>]+>', '', content_html).strip()) if content_html else 0
    logger.debug("_extract_from_html: title=%r text_len=%d", title, text_len)
    if not content_html or text_len < 200:
        logger.info("_extract_from_html: no_content (text_len=%d, title=%r)", text_len, title)
        raise ExtractError("no_content")

    logger.info("_extract_from_html: OK title=%r text_len=%d", title, text_len)
    return {"title": title, "content_html": content_html}


async def _render_via_browser(url: str) -> str:
    """Запросити відрендерений HTML у сервісу `renderer`. Кидає при недоступності."""
    async with httpx.AsyncClient(timeout=RENDER_TIMEOUT) as client:
        resp = await client.post(f"{RENDERER_URL}/render", json={"url": url})
        resp.raise_for_status()
        return resp.json()["html"]


async def fetch_and_extract(url: str) -> dict:
    """Каскад: дешевий httpx GET, а при провалі — догрузка через headless-браузер."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers=FETCH_HEADERS,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            raise ExtractError("not_html")  # бінарник/файл — браузер не допоможе

        if _looks_blocked(response.text):
            raise ExtractError("blocked")  # отримали заглушку челенджу — пробуємо браузер

        # readability — синхронний і CPU-важкий; у потік, щоб не блокувати event loop.
        return await asyncio.to_thread(_extract_from_html, response.text)

    except ExtractError as e:
        if str(e) in ("no_content", "blocked"):
            return await _fallback_or_raise(url, e)  # SPA/JS, paywall чи анти-бот
        raise
    except httpx.HTTPStatusError as e:
        if e.response.status_code in RETRYABLE_STATUSES:
            return await _fallback_or_raise(url, e)  # ймовірне bot-блокування
        raise
    except httpx.RequestError as e:
        # Таймаут, обрив/скид зʼєднання — повільний JS-сайт або bot-блок; пробуємо браузер.
        # (httpx.TimeoutException — підклас RequestError, теж сюди.)
        return await _fallback_or_raise(url, e)


async def _fallback_or_raise(url: str, original: Exception) -> dict:
    """Спробувати браузерний рендеринг; якщо він недоступний — прокинути вихідну помилку.

    Гілки:
      EXTRACT_BACKEND=browser_agent → POST у browser-agent (`generic/get_page`).
        BrowserAgentUnavailable (мережа/5xx) → auto-fallback на локальний renderer.
        ExtractError від BA (blocked/no_content/timeout) — кінцева, без fallback.
      EXTRACT_BACKEND=legacy (default) → локальний renderer.
    """
    if EXTRACT_BACKEND == "browser_agent":
        try:
            html = await fetch_html_via_get_page(url, extract_id=str(uuid4()))
        except BrowserAgentUnavailable as e:
            logger.warning("browser-agent unavailable, falling back to renderer: %s", e)
        else:
            logger.info("browser-agent returned html len=%d for url=%s", len(html), url)
            # BA повернув html; readability у потоці.
            return await asyncio.to_thread(_extract_from_html, html)

    if not RENDERER_URL:
        raise original
    try:
        html = await _render_via_browser(url)
    except Exception:
        raise original  # renderer лежить/недоступний — поводимось як без фолбеку
    if _looks_blocked(html):
        raise ExtractError("blocked")  # браузер теж не пройшов анти-бот — не зберігаємо заглушку
    return await asyncio.to_thread(_extract_from_html, html)  # може знову кинути ExtractError("no_content")
