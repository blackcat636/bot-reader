import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from .converter import generate_file, MEDIA_TYPES
from .db import (
    init_db, get_or_create_user, save_article, get_article, get_article_by_url,
    get_user_history, count_user_articles, search_articles, cleanup_expired_codes,
    create_link_code, preview_link, confirm_link,
    create_share_code, revoke_share_code, claim_share_code,
    get_user_lang, set_user_lang,
)
from .extractor import fetch_and_extract, ExtractError
from .i18n import t, normalize

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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


@app.post("/extract")
async def extract(req: ExtractRequest):
    lang = normalize(req.lang)
    await get_or_create_user(req.user_id, req.user_type, lang=lang)

    existing = await get_article_by_url(req.user_id, req.url)
    if existing:
        return {"id": existing["id"], "title": existing["title"], "url": req.url}

    try:
        article = await fetch_and_extract(req.url)
    except ExtractError as e:
        raise HTTPException(status_code=400, detail=t(lang, f"err_{e}"))
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail=t(lang, "err_timeout"))
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=t(lang, "err_http", code=e.response.status_code))
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail=t(lang, "err_request"))

    article_id = await save_article(
        req.user_id, req.url, article["title"], article["content_html"]
    )
    return {"id": article_id, "title": article["title"], "url": req.url}


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


@app.get("/articles/{article_id}/download")
async def download(article_id: int, format: str, user_id: str, lang: str = "en"):
    lang = normalize(lang)
    if format not in MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=t(lang, "err_format_invalid", formats=", ".join(MEDIA_TYPES)),
        )

    article = await get_article(article_id, user_id)
    if not article:
        raise HTTPException(status_code=404, detail=t(lang, "err_article_not_found"))

    try:
        data, filename = await generate_file(
            article["title"], article["url"], article["content_html"], format
        )
    except Exception:
        logger.exception("Error generating %s for article %d", format, article_id)
        raise HTTPException(status_code=500, detail=t(lang, "err_generate"))

    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "article"
    encoded_name = quote(filename, safe="")
    return Response(
        content=data,
        media_type=MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'},
    )


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
