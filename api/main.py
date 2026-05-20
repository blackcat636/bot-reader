import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from .converter import generate_file, MEDIA_TYPES
from .db import (
    init_db, get_or_create_user, save_article, get_article, get_user_history,
    create_link_code, preview_link, confirm_link,
    create_share_code, revoke_share_code, claim_share_code,
)
from .extractor import fetch_and_extract, ExtractError

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
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


@app.post("/extract")
async def extract(req: ExtractRequest):
    try:
        article = await fetch_and_extract(req.url)
    except ExtractError as e:
        messages = {
            "not_html": "Посилання веде не на HTML-сторінку.",
            "no_content": "Не вдалося виділити текст — можливо, paywall або авторизація.",
        }
        raise HTTPException(status_code=400, detail=messages.get(str(e), str(e)))
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="Час очікування вичерпано.")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Сервер повернув помилку: {e.response.status_code}.")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Не вдалося підключитися: {e}")

    await get_or_create_user(req.user_id, req.user_type)
    article_id = await save_article(
        req.user_id, req.url, article["title"], article["content_html"]
    )
    return {"id": article_id, "title": article["title"], "url": req.url}


@app.get("/articles/{article_id}/download")
async def download(article_id: int, format: str, user_id: str):
    if format not in MEDIA_TYPES:
        raise HTTPException(status_code=400, detail=f"Формат має бути одним з: {', '.join(MEDIA_TYPES)}")

    article = await get_article(article_id, user_id)
    if not article:
        raise HTTPException(status_code=404, detail="Стаття не знайдена.")

    try:
        data, filename = await generate_file(
            article["title"], article["url"], article["content_html"], format
        )
    except Exception:
        logger.exception("Error generating %s for article %d", format, article_id)
        raise HTTPException(status_code=500, detail="Помилка генерації файлу.")

    return Response(
        content=data,
        media_type=MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/history")
async def history(user_id: str):
    return await get_user_history(user_id)


class ShareClaimRequest(BaseModel):
    code: str
    user_id: str
    user_type: str = "browser"


@app.post("/share/generate")
async def share_generate(article_id: int, user_id: str):
    try:
        code = await create_share_code(article_id, user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Стаття не знайдена.")
    return {"code": code}


@app.delete("/share/{code}")
async def share_revoke(code: str, user_id: str):
    revoked = await revoke_share_code(code, user_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Код не знайдено або не належить вам.")
    return {"ok": True}


@app.post("/share/claim")
async def share_claim(req: ShareClaimRequest):
    try:
        article = await claim_share_code(req.code, req.user_id, req.user_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Код не знайдено або вже використано.")
    return article


class LinkConfirmRequest(BaseModel):
    code: str
    user_id: str
    user_type: str = "browser"


@app.post("/link/generate")
async def link_generate(user_id: str, user_type: str = "browser"):
    await get_or_create_user(user_id, user_type)
    code = await create_link_code(user_id)
    return {"code": code, "expires_in": 600}


@app.get("/link/preview")
async def link_preview(code: str, user_id: str, user_type: str = "browser"):
    errors = {
        "invalid_code": "Код не знайдено.",
        "expired_code": "Код застарів. Згенеруй новий.",
        "user_not_found": "Користувача не знайдено.",
    }
    try:
        return await preview_link(code, user_id, user_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=errors.get(str(e), str(e)))


@app.post("/link/confirm")
async def link_confirm(req: LinkConfirmRequest):
    errors = {
        "invalid_code": "Код не знайдено.",
        "expired_code": "Код застарів. Згенеруй новий.",
        "same_group": "Ці акаунти вже пов'язані.",
        "user_not_found": "Користувача не знайдено.",
    }
    try:
        merged = await confirm_link(req.code, req.user_id, req.user_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=errors.get(str(e), str(e)))
    return {"merged_users": merged}
