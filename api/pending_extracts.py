"""In-memory реєстр Future для очікування результату парсингу від browser-agent.

Кожен виклик `fetch_html_via_get_page` реєструє унікальний `extract_id` → Future.
Callback endpoint `/internal/parser-callback` резолвить Future, коли BA повідомляє
про завершення задачі. Якщо callback не дійшов — poll-fallback резолвить ту ж Future.

Cleanup-task викидає Future, що чекають довше ніж 2× wait timeout (захист від
зависання при незвичайних збоях, напр. рестарті api між POST /api/run і callback).
"""
import asyncio
import time
from dataclasses import dataclass


@dataclass
class _Entry:
    future: asyncio.Future
    created_at: float


class PendingExtracts:
    def __init__(self):
        self._d: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def register(self, extract_id: str) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        async with self._lock:
            self._d[extract_id] = _Entry(future=fut, created_at=time.monotonic())
        return fut

    async def resolve(self, extract_id: str, html: str) -> None:
        """Ідемпотентно: callback + poll можуть прийти обидва."""
        async with self._lock:
            entry = self._d.pop(extract_id, None)
        if entry and not entry.future.done():
            entry.future.set_result(html)

    async def reject(self, extract_id: str, exc: BaseException) -> None:
        async with self._lock:
            entry = self._d.pop(extract_id, None)
        if entry and not entry.future.done():
            entry.future.set_exception(exc)

    async def discard(self, extract_id: str) -> None:
        """Прибрати запис без резолву (напр. при локальному timeout)."""
        async with self._lock:
            self._d.pop(extract_id, None)

    async def cleanup_loop(self, max_age_sec: float) -> None:
        while True:
            await asyncio.sleep(60)
            cutoff = time.monotonic() - max_age_sec
            stale: list[str] = []
            async with self._lock:
                for eid, entry in self._d.items():
                    if entry.created_at < cutoff:
                        stale.append(eid)
                for eid in stale:
                    entry = self._d.pop(eid)
                    if not entry.future.done():
                        entry.future.set_exception(
                            TimeoutError(f"pending extract {eid} expired")
                        )


pending_extracts = PendingExtracts()
