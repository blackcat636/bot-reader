"""Мінімальний async-клієнт telegra.ph.

Акаунт створюється ліниво при першій публікації; токен тримається в пам'яті
процесу (втрата при рестарті некритична — вже опубліковані сторінки лишаються
доступними за своїм URL, нам потрібен лише запис, не редагування). За потреби
можна задати готовий токен через ENV TELEGRAPH_TOKEN.
"""
import asyncio
import json
import os

import httpx

TELEGRAPH_API = "https://api.telegra.ph"

_token: str | None = os.getenv("TELEGRAPH_TOKEN") or None
_token_lock = asyncio.Lock()


class TelegraphError(Exception):
    pass


async def _ensure_token(client: httpx.AsyncClient) -> str:
    global _token
    if _token:
        return _token
    async with _token_lock:
        if _token:  # хтось створив, поки ми чекали лок
            return _token
        resp = await client.post(
            f"{TELEGRAPH_API}/createAccount",
            data={"short_name": "ReaderBot", "author_name": "Reader Bot"},
        )
        data = resp.json()
        if not data.get("ok"):
            raise TelegraphError(data.get("error", "createAccount failed"))
        _token = data["result"]["access_token"]
        return _token


async def create_page(title: str, nodes: list, author_name: str = "Reader Bot") -> str:
    """Публікує сторінку й повертає її telegra.ph URL."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _ensure_token(client)
        resp = await client.post(
            f"{TELEGRAPH_API}/createPage",
            data={
                "access_token": token,
                "title": (title or "Article")[:256],
                "author_name": author_name,
                "content": json.dumps(nodes, ensure_ascii=False),
                "return_content": "false",
            },
        )
        data = resp.json()
        if not data.get("ok"):
            raise TelegraphError(data.get("error", "createPage failed"))
        return data["result"]["url"]
