"""Клієнт до browser-agent (https://github.com/.../browser-agent), сценарій `generic/get_page`.

Контракт:
- POST /api/run → 202 {task_id}; виконання асинхронне.
- Результат для get_page приходить або плоским callback (chunkType="done",
  result.html), або через GET /api/tasks/{task_id} (task.result.html).
- callback_context echo-ується у кожному chunk як поле `context` — туди кладемо
  extractId, щоб резолвити правильну Future.

Реалізація: callback-first, з poll-fallback на випадок коли callback не дійшов
(firewall, рестарт api). Виняток BrowserAgentUnavailable — сигнал для extractor.py
сповзти на локальний renderer.
"""
import asyncio
import logging
import os
from typing import Optional

import httpx

from .errors import ExtractError
from .pending_extracts import pending_extracts

logger = logging.getLogger(__name__)


BROWSER_AGENT_URL = os.getenv("BROWSER_AGENT_URL", "").rstrip("/")
BROWSER_AGENT_API_KEY = os.getenv("BROWSER_AGENT_API_KEY", "")
PARSER_CALLBACK_BASE_URL = os.getenv("PARSER_CALLBACK_BASE_URL", "").rstrip("/")
PARSER_CALLBACK_SECRET = os.getenv("PARSER_CALLBACK_SECRET", "")
PARSER_WAIT_TIMEOUT_SEC = float(os.getenv("PARSER_WAIT_TIMEOUT_SEC", "110"))
PARSER_POLL_INTERVAL_SEC = float(os.getenv("PARSER_POLL_INTERVAL_SEC", "2"))
PARSER_MAX_BYTES = int(os.getenv("PARSER_MAX_BYTES", "500000"))
PARSER_FORCE_TIER = os.getenv("PARSER_FORCE_TIER", "").strip()


class BrowserAgentUnavailable(Exception):
    """BA недосяжний (мережа / 5xx / не-2xx на POST /api/run). Сигнал для auto-fallback."""


def _looks_blocked(html: str) -> bool:
    low = html.lower()
    return "cf_chl_opt" in low or "cdn-cgi/challenge-platform" in low


def _build_run_payload(url: str, extract_id: str) -> dict:
    payload: dict = {
        "platform": "generic",
        "action": "run_scenario",
        "params": {
            "scenario": "get_page",
            "input": {
                "url": url,
                "includeHtml": True,
                "includeText": False,
                "maxBytes": PARSER_MAX_BYTES,
            },
        },
        "callback_context": {"extractId": extract_id},
    }
    if PARSER_FORCE_TIER:
        payload["params"]["forceTier"] = PARSER_FORCE_TIER
    if PARSER_CALLBACK_BASE_URL:
        payload["callback_url"] = f"{PARSER_CALLBACK_BASE_URL}/internal/parser-callback"
        if PARSER_CALLBACK_SECRET:
            payload["callback_headers"] = {
                "Authorization": f"Bearer {PARSER_CALLBACK_SECRET}"
            }
    return payload


async def _submit_task(client: httpx.AsyncClient, url: str, extract_id: str) -> str:
    headers = {"Content-Type": "application/json"}
    if BROWSER_AGENT_API_KEY:
        headers["X-API-Key"] = BROWSER_AGENT_API_KEY
    try:
        resp = await client.post(
            f"{BROWSER_AGENT_URL}/api/run",
            json=_build_run_payload(url, extract_id),
            headers=headers,
            timeout=30,
        )
    except httpx.RequestError as e:
        raise BrowserAgentUnavailable(f"POST /api/run failed: {e}") from e
    if resp.status_code >= 500 or resp.status_code in (502, 503, 504):
        raise BrowserAgentUnavailable(f"BA returned {resp.status_code}")
    if resp.status_code != 202:
        # 4xx — це наша проблема (поганий запит), не «BA лежить». Без fallback.
        raise ExtractError("request_error")
    try:
        task_id = resp.json()["task_id"]
    except (KeyError, ValueError) as e:
        raise BrowserAgentUnavailable(f"bad /api/run response: {e}") from e
    return task_id


async def _poll_task(client: httpx.AsyncClient, task_id: str, extract_id: str) -> None:
    """Крутиться до резолву Future; виходить тихо, якщо Future вже резолвлена callback'ом."""
    headers = {}
    if BROWSER_AGENT_API_KEY:
        headers["X-API-Key"] = BROWSER_AGENT_API_KEY
    while True:
        await asyncio.sleep(PARSER_POLL_INTERVAL_SEC)
        try:
            resp = await client.get(
                f"{BROWSER_AGENT_URL}/api/tasks/{task_id}",
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            task = resp.json().get("task") or {}
        except (httpx.HTTPError, ValueError):
            continue  # мережа моргнула; чекаємо callback або наступний poll-тік
        status = task.get("status")
        if status == "completed":
            html = (task.get("result") or {}).get("html") or ""
            await pending_extracts.resolve(extract_id, html)
            return
        if status == "failed":
            err = str(task.get("error") or "").lower()
            code = "blocked" if "block" in err or "captcha" in err or "challenge" in err else "no_content"
            await pending_extracts.reject(extract_id, ExtractError(code))
            return


async def fetch_html_via_get_page(url: str, extract_id: str) -> str:
    """Отримати raw HTML через browser-agent.

    Підняти:
      BrowserAgentUnavailable — BA не відповів / 5xx → caller робить fallback на renderer.
      ExtractError("blocked"|"no_content"|"timeout"|"request_error") — кінцева помилка.
    """
    if not BROWSER_AGENT_URL:
        raise BrowserAgentUnavailable("BROWSER_AGENT_URL is empty")

    future = await pending_extracts.register(extract_id)
    poll_task: Optional[asyncio.Task] = None
    async with httpx.AsyncClient() as client:
        try:
            task_id = await _submit_task(client, url, extract_id)
            logger.info("browser-agent task %s submitted for extract %s", task_id, extract_id)

            poll_task = asyncio.create_task(_poll_task(client, task_id, extract_id))
            try:
                html = await asyncio.wait_for(future, timeout=PARSER_WAIT_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                await pending_extracts.discard(extract_id)
                raise ExtractError("timeout")
        finally:
            if poll_task and not poll_task.done():
                poll_task.cancel()
                try:
                    await poll_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # На випадок ранньої помилки до wait_for — прибрати запис.
            await pending_extracts.discard(extract_id)

    if not html:
        logger.warning("browser-agent extract %s: empty html → no_content", extract_id)
        raise ExtractError("no_content")
    logger.info(
        "browser-agent extract %s: received html len=%d preview=%r",
        extract_id, len(html), html[:200],
    )
    return html
