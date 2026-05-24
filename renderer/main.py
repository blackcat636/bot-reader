"""Headless-браузерний рендерер на camoufox (анти-детект Firefox).

Єдина задача: отримати URL, відрендерити сторінку, пройти анти-бот челендж
(включно з клікабельним Cloudflare Turnstile) і повернути сирий HTML. Витяг
тексту (readability) і збереження живуть в `api/`, не тут.

Чому camoufox, а не звичайний Playwright chromium: Cloudflare managed Turnstile
детектить headless/автоматизований chromium за фінгерпринтом і не пропускає.
camoufox (патчений Firefox) має чистий фінгерпринт, тож після кліку по чекбоксу
Turnstile челендж резолвиться.

Браузер запускається СВІЖИЙ на кожен запит: рендер — це фолбек (рідкісний,
низький об'єм), а довгоживучий Firefox виявився нестабільним між запитами.
"""
import logging
import os

from fastapi import FastAPI
from pydantic import BaseModel
from camoufox.async_api import AsyncCamoufox
from playwright.async_api import Error as PWError

from .solver import solve_if_present

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("renderer")

NAV_TIMEOUT_MS = int(os.getenv("RENDER_NAV_TIMEOUT_MS", "22000"))
# Скільки «домагатися» резолву челенджу за одну спробу (клік + очікування). Turnstile-фрейм
# зʼявляється не одразу (~6-10с), після кліку верифікація триває ще кілька секунд.
CHALLENGE_TOTAL_MS = int(os.getenv("RENDER_CHALLENGE_WAIT_MS", "16000"))
CHALLENGE_STEP_MS = 3000
SETTLE_MS = int(os.getenv("RENDER_SETTLE_MS", "2500"))
# camoufox рандомізує фінгерпринт на кожен запуск: одні проходять Turnstile, інші ні.
# Тож ретраїмо зі свіжим браузером — і на краш, і коли челендж не пройдено.
RENDER_ATTEMPTS = int(os.getenv("RENDER_ATTEMPTS", "3"))

# Маркери, що однозначно вказують на сторінку-інтерстиціал Cloudflare (а не на
# легітимний Turnstile-віджет у формі коментарів/логіну, який лишає
# challenges.cloudflare.com у звичайній статті — тому той маркер НЕ беремо).
CHALLENGE_MARKERS = (
    "cf_chl_opt",
    "cdn-cgi/challenge-platform",
)

app = FastAPI()


class RenderRequest(BaseModel):
    url: str


class RenderResponse(BaseModel):
    html: str
    status: int
    final_url: str
    title: str


@app.get("/health")
async def health():
    return {"ok": True}


async def _is_challenge(page) -> bool:
    # Під час челенджу сторінка перезавантажується — виклик може впасти з
    # "Execution context was destroyed"; трактуємо це як «ще челендж».
    try:
        html = (await page.content()).lower()
        return any(m in html for m in CHALLENGE_MARKERS)
    except PWError:
        return True


async def _click_turnstile(page) -> bool:
    """Клікнути по чекбоксу Cloudflare Turnstile. Iframe вкладений, тож дістаємось
    до нього через список фреймів, а не селектором у головному документі."""
    for f in page.frames:
        if "challenges.cloudflare.com" not in f.url:
            continue
        try:
            el = await f.frame_element()
            box = await el.bounding_box()
            if box:
                # Чекбокс — ліворуч у віджеті, по вертикалі центр.
                await page.mouse.click(box["x"] + 28, box["y"] + box["height"] / 2)
                return True
        except PWError:
            pass
    return False


async def _resolve_challenge(page) -> None:
    deadline = CHALLENGE_TOTAL_MS
    while deadline > 0 and await _is_challenge(page):
        await _click_turnstile(page)
        try:
            await page.wait_for_timeout(CHALLENGE_STEP_MS)
        except PWError:
            pass
        deadline -= CHALLENGE_STEP_MS
    # Якщо лишилася інтерактивна капча (reCAPTCHA/hCaptcha) — опційний платний
    # солвер (вимкнений за замовчуванням).
    if await _is_challenge(page):
        await solve_if_present(page)


async def _render(page, url: str) -> RenderResponse:
    status = 0
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        status = resp.status if resp else 0
    except PWError:
        status = 0  # частковий рендер усе одно може бути корисним

    if await _is_challenge(page):
        await _resolve_challenge(page)

    try:
        await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    except PWError:
        pass

    try:
        html = await page.content()
    except PWError:
        await page.wait_for_load_state("domcontentloaded", timeout=SETTLE_MS)
        html = await page.content()
    try:
        title = await page.title() or ""
    except PWError:
        title = ""
    return RenderResponse(html=html, status=status, final_url=page.url, title=title)


async def _render_once(url: str) -> RenderResponse:
    # Свіжий браузер на кожну спробу. Вхід/вихід контексту вручну: camoufox/Firefox
    # нерідко завершується нечисто ("Browser.close: handler is closed") — цю помилку
    # ковтаємо, щоб не перетворити вже успішний рендер на збій.
    cm = AsyncCamoufox(headless=True, humanize=True, locale="uk-UA")
    browser = await cm.__aenter__()
    try:
        page = await browser.new_page()
        # Тихо споживаємо page errors/краші на рівні Python. УВАГА: це НЕ запобігає
        # краху Node-драйвера на challenge-сторінках без pageError.location — той
        # баг прибирається патчем coreBundle.js у renderer/Dockerfile, не тут.
        page.on("pageerror", lambda e: None)
        page.on("crash", lambda p: None)
        return await _render(page, url)
    finally:
        try:
            await cm.__aexit__(None, None, None)
        except Exception as e:
            log.warning("browser close error ignored: %r", e)


def _html_blocked(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in CHALLENGE_MARKERS)


@app.post("/render", response_model=RenderResponse)
async def render(req: RenderRequest):
    last_exc = None
    last_res = None
    for attempt in range(1, RENDER_ATTEMPTS + 1):
        try:
            res = await _render_once(req.url)
        except Exception as e:
            last_exc = e
            log.warning("render attempt %d/%d crashed: %r", attempt, RENDER_ATTEMPTS, repr(e)[:120])
            continue
        if _html_blocked(res.html):
            # Челендж не пройдено — наступна спроба дасть новий фінгерпринт.
            last_res = res
            log.warning("render attempt %d/%d still challenged", attempt, RENDER_ATTEMPTS)
            continue
        return res
    if last_res is not None:
        return last_res  # усі спроби впёрлись у челендж — api позначить як blocked
    raise last_exc
