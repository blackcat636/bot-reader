import asyncio
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from .converter import generate_file, MEDIA_TYPES
from .db import (
    init_db, get_or_create_user, save_article, get_article, get_article_by_url, delete_article,
    get_user_history, count_user_articles, search_articles, cleanup_expired_codes,
    create_link_code, preview_link, confirm_link,
    create_share_code, revoke_share_code, claim_share_code,
    get_user_lang, set_user_lang,
    touch_user, is_user_banned, set_user_banned, list_users, count_users,
    get_admin_stats, log_failed_url, list_failed_urls, count_failed_urls,
    get_article_any, delete_article_any, set_article_telegraph_url,
)
from .browser_agent import PARSER_CALLBACK_SECRET, PARSER_WAIT_TIMEOUT_SEC
from .errors import ExtractError
from .extractor import fetch_and_extract
from .i18n import t, normalize
from .pending_extracts import pending_extracts
from .telegram_view import html_to_chunks, html_to_telegraph_nodes
from .telegraph import create_page, TelegraphError

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Telegram id адмінів, через кому. Спільний з ботом (той самий ENV).
ADMIN_IDS = {a.strip() for a in os.getenv("ADMIN_IDS", "").split(",") if a.strip()}


def _require_admin(admin_id: str) -> None:
    if admin_id not in ADMIN_IDS:
        raise HTTPException(status_code=403, detail="forbidden")


async def _cleanup_loop():
    while True:
        await asyncio.sleep(3600)
        removed = await cleanup_expired_codes()
        if removed:
            logger.info("Cleaned up %d expired link codes", removed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(_cleanup_loop())
    # cleanup протухлих Future у реєстрі pending_extracts (browser-agent fallback).
    # max_age = 2× wait timeout: гарантовано довше за нормальне завершення задачі.
    asyncio.create_task(pending_extracts.cleanup_loop(PARSER_WAIT_TIMEOUT_SEC * 2))
    yield


app = FastAPI(title="Reader API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExtractRequest(BaseModel):
    url: str
    user_id: str
    user_type: str = "browser"
    lang: str = "en"
    username: str | None = None


@app.post("/extract")
async def extract(req: ExtractRequest):
    lang = normalize(req.lang)
    await get_or_create_user(req.user_id, req.user_type, lang=lang)
    await touch_user(req.user_id, req.username)

    if await is_user_banned(req.user_id):
        raise HTTPException(status_code=403, detail=t(lang, "err_banned"))

    existing = await get_article_by_url(req.user_id, req.url)
    if existing:
        return {"id": existing["id"], "title": existing["title"], "url": req.url}

    try:
        article = await fetch_and_extract(req.url)
    except ExtractError as e:
        await log_failed_url(req.user_id, req.url, str(e))
        raise HTTPException(status_code=400, detail=t(lang, f"err_{e}"))
    except httpx.TimeoutException:
        await log_failed_url(req.user_id, req.url, "timeout")
        raise HTTPException(status_code=408, detail=t(lang, "err_timeout"))
    except httpx.HTTPStatusError as e:
        await log_failed_url(req.user_id, req.url, f"http_{e.response.status_code}")
        raise HTTPException(status_code=502, detail=t(lang, "err_http", code=e.response.status_code))
    except httpx.RequestError:
        await log_failed_url(req.user_id, req.url, "request_error")
        raise HTTPException(status_code=502, detail=t(lang, "err_request"))

    article_id = await save_article(
        req.user_id, req.url, article["title"], article["content_html"]
    )
    return {"id": article_id, "title": article["title"], "url": req.url}


@app.post("/internal/parser-callback")
async def parser_callback(request: Request):
    """Callback від browser-agent із результатом `generic/get_page`.

    Лише фінальні chunks (`finished: true`) резолвлять Future в pending_extracts;
    проміжні батчі (для discovery-сценаріїв) ігноруються. Завжди повертаємо 200,
    щоб BA не ретраїв даремно.
    """
    if PARSER_CALLBACK_SECRET:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {PARSER_CALLBACK_SECRET}":
            raise HTTPException(status_code=401, detail="unauthorized")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid json")

    extract_id = (body.get("context") or {}).get("extractId")
    if not extract_id or not body.get("finished"):
        return {"ok": True}  # проміжний батч або не наша задача

    chunk_type = body.get("chunkType")
    logger.info("parser-callback extract=%s chunkType=%s", extract_id, chunk_type)

    if chunk_type == "done":
        html = body.get("html") or ""
        _skip = {"chunkType", "finished", "engineUsed", "context"}
        _loggable = {k: (f"<html len={len(v)}>" if k == "html" and isinstance(v, str) else v) for k, v in body.items() if k not in _skip}
        logger.info("parser-callback fields: %s", _loggable)
        await pending_extracts.resolve(extract_id, html)
    else:
        err = str(body.get("error") or "").lower()
        code = "blocked" if "block" in err or "captcha" in err or "challenge" in err else "no_content"
        logger.warning("parser-callback extract=%s chunkType=%s error=%r → %s", extract_id, chunk_type, body.get("error"), code)
        await pending_extracts.reject(extract_id, ExtractError(code))
    return {"ok": True}


@app.get("/articles/{article_id}")
async def get_article_info(article_id: int, user_id: str, lang: str = "en"):
    lang = normalize(lang)
    article = await get_article(article_id, user_id)
    if not article:
        raise HTTPException(status_code=404, detail=t(lang, "err_article_not_found"))
    return {
        "id": article["id"],
        "title": article["title"],
        "url": article["url"],
        "created_at": article["created_at"],
    }


@app.delete("/articles/{article_id}")
async def delete_article_endpoint(article_id: int, user_id: str, lang: str = "en"):
    lang = normalize(lang)
    deleted = await delete_article(article_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=t(lang, "err_article_not_found"))
    return {"ok": True}


async def _render_download(article: dict, format: str, lang: str) -> Response:
    """Генерує файл зі статті у потрібному форматі та повертає Response.
    Спільне для звичайного й адмінського download (вони різняться лише авторизацією)."""
    if format not in MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=t(lang, "err_format_invalid", formats=", ".join(MEDIA_TYPES)),
        )
    try:
        data, filename = await asyncio.to_thread(
            generate_file, article["title"], article["url"], article["content_html"], format
        )
    except Exception:
        logger.exception("Error generating %s for article %d", format, article["id"])
        raise HTTPException(status_code=500, detail=t(lang, "err_generate"))

    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "article"
    encoded_name = quote(filename, safe="")
    return Response(
        content=data,
        media_type=MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'},
    )


@app.get("/articles/{article_id}/download")
async def download(article_id: int, format: str, user_id: str, lang: str = "en"):
    lang = normalize(lang)
    article = await get_article(article_id, user_id)
    if not article:
        raise HTTPException(status_code=404, detail=t(lang, "err_article_not_found"))
    return await _render_download(article, format, lang)


@app.get("/articles/{article_id}/read")
async def read_article(article_id: int, user_id: str, lang: str = "en"):
    """Стаття як список Telegram-HTML повідомлень для читання прямо в чаті."""
    lang = normalize(lang)
    article = await get_article(article_id, user_id)
    if not article:
        raise HTTPException(status_code=404, detail=t(lang, "err_article_not_found"))
    # lxml-парсинг CPU-bound — у потік, як і генерація файлів.
    chunks = await asyncio.to_thread(
        html_to_chunks, article["content_html"], article["title"], article["url"]
    )
    return {"chunks": chunks}


@app.post("/articles/{article_id}/telegraph")
async def telegraph_article(article_id: int, user_id: str, lang: str = "en"):
    """Публікує статтю на telegra.ph (для Instant View). Кешує URL у БД."""
    lang = normalize(lang)
    article = await get_article(article_id, user_id)
    if not article:
        raise HTTPException(status_code=404, detail=t(lang, "err_article_not_found"))
    if article.get("telegraph_url"):
        return {"url": article["telegraph_url"]}

    nodes = await asyncio.to_thread(
        html_to_telegraph_nodes, article["content_html"], article["url"]
    )
    try:
        url = await create_page(article["title"], nodes)
    except (TelegraphError, httpx.HTTPError):
        logger.exception("Telegraph publish failed for article %d", article_id)
        raise HTTPException(status_code=502, detail=t(lang, "err_telegraph"))

    await set_article_telegraph_url(article_id, url)
    return {"url": url}


@app.get("/history")
async def history(user_id: str, limit: int = 10, offset: int = 0):
    articles = await get_user_history(user_id, limit=limit, offset=offset)
    total = await count_user_articles(user_id)
    return {"items": articles, "total": total, "offset": offset, "limit": limit}


@app.get("/search")
async def search(user_id: str, q: str, limit: int = 10):
    return await search_articles(user_id, q, limit=limit)


class ShareClaimRequest(BaseModel):
    code: str
    user_id: str
    user_type: str = "browser"
    lang: str = "en"


@app.post("/share/generate")
async def share_generate(article_id: int, user_id: str, lang: str = "en"):
    lang = normalize(lang)
    try:
        code = await create_share_code(article_id, user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=t(lang, "err_article_not_found"))
    return {"code": code}


@app.delete("/share/{code}")
async def share_revoke(code: str, user_id: str, lang: str = "en"):
    lang = normalize(lang)
    revoked = await revoke_share_code(code, user_id)
    if not revoked:
        raise HTTPException(status_code=404, detail=t(lang, "err_share_not_owner"))
    return {"ok": True}


@app.post("/share/claim")
async def share_claim(req: ShareClaimRequest):
    lang = normalize(req.lang)
    try:
        article = await claim_share_code(req.code, req.user_id, req.user_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=t(lang, "err_share_invalid"))
    return article


class LinkConfirmRequest(BaseModel):
    code: str
    user_id: str
    user_type: str = "browser"
    lang: str = "en"


@app.post("/link/generate")
async def link_generate(user_id: str, user_type: str = "browser", lang: str = "en"):
    lang = normalize(lang)
    await get_or_create_user(user_id, user_type)
    try:
        code = await create_link_code(user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=t(lang, f"err_{e}"))
    return {"code": code, "expires_in": 600}


@app.get("/link/preview")
async def link_preview(code: str, user_id: str, user_type: str = "browser", lang: str = "en"):
    lang = normalize(lang)
    try:
        return await preview_link(code, user_id, user_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=t(lang, f"err_{e}"))


@app.post("/link/confirm")
async def link_confirm(req: LinkConfirmRequest):
    lang = normalize(req.lang)
    try:
        merged = await confirm_link(req.code, req.user_id, req.user_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=t(lang, f"err_{e}"))
    return {"merged_users": merged}


@app.get("/users/{user_id}")
async def get_user_info(user_id: str):
    lang = await get_user_lang(user_id)
    return {"user_id": user_id, "lang": lang}


@app.patch("/users/{user_id}/lang")
async def patch_user_lang(user_id: str, lang: str, user_type: str = "browser"):
    normalized = normalize(lang)
    await get_or_create_user(user_id, user_type, lang=normalized)
    await set_user_lang(user_id, normalized)
    return {"lang": normalized}


# --- Admin --------------------------------------------------------------
# Усі /admin/* вимагають admin_id з ADMIN_IDS (ENV, спільний з ботом).

@app.get("/admin/whoami")
async def admin_whoami(admin_id: str):
    return {"is_admin": admin_id in ADMIN_IDS}


@app.get("/admin/stats")
async def admin_stats(admin_id: str):
    _require_admin(admin_id)
    return await get_admin_stats()


@app.get("/admin/users")
async def admin_users(admin_id: str, limit: int = 10, offset: int = 0):
    _require_admin(admin_id)
    items = await list_users(limit=limit, offset=offset)
    total = await count_users()
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.post("/admin/users/{user_id}/ban")
async def admin_ban(user_id: str, admin_id: str):
    _require_admin(admin_id)
    if not await set_user_banned(user_id, True):
        raise HTTPException(status_code=404, detail="user_not_found")
    return {"ok": True, "banned": True}


@app.post("/admin/users/{user_id}/unban")
async def admin_unban(user_id: str, admin_id: str):
    _require_admin(admin_id)
    if not await set_user_banned(user_id, False):
        raise HTTPException(status_code=404, detail="user_not_found")
    return {"ok": True, "banned": False}


@app.get("/admin/failed")
async def admin_failed(admin_id: str, limit: int = 10, offset: int = 0):
    _require_admin(admin_id)
    items = await list_failed_urls(limit=limit, offset=offset)
    total = await count_failed_urls()
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/admin/users/{user_id}/articles")
async def admin_user_articles(user_id: str, admin_id: str, limit: int = 10, offset: int = 0):
    _require_admin(admin_id)
    # get_user_history джойнить через групу юзера → це всі статті, видимі цьому юзеру.
    items = await get_user_history(user_id, limit=limit, offset=offset)
    total = await count_user_articles(user_id)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/admin/articles/{article_id}")
async def admin_article_info(article_id: int, admin_id: str):
    _require_admin(admin_id)
    article = await get_article_any(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="article_not_found")
    return {
        "id": article["id"],
        "title": article["title"],
        "url": article["url"],
        "created_at": article["created_at"],
    }


@app.get("/admin/articles/{article_id}/download")
async def admin_article_download(article_id: int, format: str, admin_id: str, lang: str = "en"):
    _require_admin(admin_id)
    lang = normalize(lang)
    article = await get_article_any(article_id)
    if not article:
        raise HTTPException(status_code=404, detail=t(lang, "err_article_not_found"))
    return await _render_download(article, format, lang)


@app.delete("/admin/articles/{article_id}")
async def admin_article_delete(article_id: int, admin_id: str):
    _require_admin(admin_id)
    if not await delete_article_any(article_id):
        raise HTTPException(status_code=404, detail="article_not_found")
    return {"ok": True}
