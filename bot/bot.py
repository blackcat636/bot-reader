import asyncio
import logging
import os
from urllib.parse import urlparse, unquote

import httpx
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

from api.i18n import t, normalize

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_URL = os.getenv("API_URL", "http://api:8000")
ADMIN_IDS = {a.strip() for a in os.getenv("ADMIN_IDS", "").split(",") if a.strip()}


def is_admin(user_id) -> bool:
    return str(user_id) in ADMIN_IDS

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def get_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    if "lang" not in context.user_data:
        raw = getattr(update.effective_user, "language_code", None)
        context.user_data["lang"] = normalize(raw)
    return context.user_data["lang"]


def format_keyboard(article_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, "format_read"), callback_data=f"read:{article_id}"),
            InlineKeyboardButton(t(lang, "format_telegraph"), callback_data=f"tgph:{article_id}"),
        ],
        [
            InlineKeyboardButton(t(lang, "format_pdf"), callback_data=f"fmt:pdf:{article_id}"),
            InlineKeyboardButton(t(lang, "format_md"), callback_data=f"fmt:md:{article_id}"),
        ],
        [
            InlineKeyboardButton(t(lang, "format_html"), callback_data=f"fmt:html:{article_id}"),
            InlineKeyboardButton(t(lang, "format_epub"), callback_data=f"fmt:epub:{article_id}"),
        ],
        [
            InlineKeyboardButton(t(lang, "format_share"), callback_data=f"share:{article_id}"),
        ],
        [
            InlineKeyboardButton(t(lang, "format_delete"), callback_data=f"del:{article_id}"),
        ],
    ])


def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    await update.message.reply_text(t(lang, "start"), parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    await update.message.reply_text(t(lang, "help"), parse_mode="HTML")


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    await update.message.reply_text(
        t(lang, "settings_prompt"),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "settings_lang_en"), callback_data="setlang:en"),
            InlineKeyboardButton(t(lang, "settings_lang_uk"), callback_data="setlang:uk"),
        ]])
    )


async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    article_id = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/articles/{article_id}",
            params={"user_id": user_id, "lang": lang},
        )

    if resp.status_code == 404:
        await query.edit_message_text(t(lang, "article_not_found"))
        return

    title = resp.json()["title"]
    await query.edit_message_text(
        t(lang, "delete_confirm", title=title),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "delete_confirm_btn"), callback_data=f"delconfirm:{article_id}"),
            InlineKeyboardButton(t(lang, "link_cancel_btn"), callback_data=f"delcancel:{article_id}"),
        ]]),
    )


async def handle_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    article_id = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{API_URL}/articles/{article_id}",
            params={"user_id": user_id, "lang": lang},
        )

    if resp.status_code == 200:
        await query.edit_message_text(t(lang, "delete_done"))
    else:
        await query.edit_message_text(t(lang, "article_not_found"))


async def handle_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    article_id = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/articles/{article_id}",
            params={"user_id": user_id, "lang": lang},
        )

    if resp.status_code == 404:
        await query.edit_message_text(t(lang, "article_not_found"))
        return

    article = resp.json()
    await query.edit_message_text(
        t(lang, "article_ready", title=article["title"]),
        parse_mode="HTML",
        reply_markup=format_keyboard(article_id, lang),
    )


async def _clear_awaiting_states(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_search", None)


async def handle_findcancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("awaiting_search", None)
    lang = get_lang(update, context)
    await query.edit_message_text(t(lang, "link_cancelled"))


async def handle_setlang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    new_lang = query.data.split(":")[1]
    context.user_data["lang"] = new_lang
    user_id = str(query.from_user.id)
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{API_URL}/users/{user_id}/lang",
            params={"lang": new_lang, "user_type": "telegram"},
        )
    await query.edit_message_text(t(new_lang, "settings_lang_set"))


async def link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    user_id = str(update.effective_user.id)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/link/generate",
            params={"user_id": user_id, "user_type": "telegram", "lang": lang},
        )
    if resp.status_code != 200:
        await update.message.reply_text(t(lang, "link_generate_error"))
        return
    code = resp.json()["code"]
    await update.message.reply_text(t(lang, "link_generated", code=code), parse_mode="HTML")


async def handle_link_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = update.message.text.strip().upper()
    lang = get_lang(update, context)
    user_id = str(update.effective_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/link/preview",
            params={"code": code, "user_id": user_id, "user_type": "telegram", "lang": lang},
        )

    if resp.status_code != 200:
        await update.message.reply_text(f"❌ {resp.json().get('detail', 'Error.')}")
        return

    data = resp.json()

    if data.get("already_same_group"):
        await update.message.reply_text(t(lang, "link_already_same"))
        return

    text = t(
        lang, "link_preview",
        your_users=data["your_group_users"],
        your_articles=data["your_group_articles"],
        code_users=data["code_group_users"],
        code_articles=data["code_group_articles"],
        total_users=data["total_users"],
        total_articles=data["total_articles"],
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(t(lang, "link_confirm_btn"), callback_data=f"linkconfirm:{code}"),
        InlineKeyboardButton(t(lang, "link_cancel_btn"), callback_data="linkcancel"),
    ]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def handle_share_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)

    article_id = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/share/generate",
            params={"article_id": article_id, "user_id": user_id, "lang": lang},
        )

    if resp.status_code != 200:
        await query.answer(t(lang, "link_generate_error"), show_alert=True)
        return

    code = resp.json()["code"]
    await query.message.reply_text(
        t(lang, "share_generated", code=code),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "share_revoke_btn"), callback_data=f"sharerevoke:{code}:{user_id}"),
        ]]),
    )


async def handle_share_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)

    parts = query.data.split(":")
    code, user_id = parts[1], parts[2]

    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{API_URL}/share/{code}",
            params={"user_id": user_id, "lang": lang},
        )

    if resp.status_code == 200:
        await query.edit_message_text(t(lang, "share_revoked"))
    else:
        await query.edit_message_text(t(lang, "share_revoke_error"))


async def handle_share_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = update.message.text.strip().upper()
    lang = get_lang(update, context)
    user_id = str(update.effective_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/share/claim",
            json={"code": code, "user_id": user_id, "user_type": "telegram", "lang": lang},
        )

    if resp.status_code != 200:
        await update.message.reply_text(f"❌ {resp.json().get('detail', 'Error.')}")
        return

    article = resp.json()
    await update.message.reply_text(
        t(lang, "share_received", title=article["title"]),
        parse_mode="HTML",
        reply_markup=format_keyboard(article["id"], lang),
    )


async def handle_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)

    if query.data == "linkcancel":
        await query.edit_message_text(t(lang, "link_cancelled"))
        return

    code = query.data.split(":")[1]
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/link/confirm",
            json={"code": code, "user_id": user_id, "user_type": "telegram", "lang": lang},
        )

    if resp.status_code != 200:
        await query.edit_message_text(f"❌ {resp.json().get('detail', 'Error.')}")
        return

    merged = resp.json()["merged_users"]
    await query.edit_message_text(t(lang, "link_merged", count=merged))


PAGE_SIZE = 8


def _history_keyboard(articles: list, offset: int, total: int, lang: str = "en") -> InlineKeyboardMarkup:
    buttons = []
    for a in articles:
        date = a["created_at"][:10]
        label = f"{a['title'][:35]}… ({date})" if len(a["title"]) > 35 else f"{a['title']} ({date})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"hist:{a['id']}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(t(lang, "nav_prev"), callback_data=f"histpage:{offset - PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(t(lang, "nav_next"), callback_data=f"histpage:{offset + PAGE_SIZE}"))
    if nav:
        buttons.append(nav)

    return InlineKeyboardMarkup(buttons)


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    user_id = str(update.effective_user.id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_URL}/history", params={"user_id": user_id, "limit": PAGE_SIZE, "offset": 0})
    data = resp.json()

    if not data["items"]:
        await update.message.reply_text(t(lang, "history_empty"))
        return

    total = data["total"]
    await update.message.reply_text(
        t(lang, "history_header", total=total),
        reply_markup=_history_keyboard(data["items"], 0, total, lang),
    )


async def handle_history_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)

    offset = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/history",
            params={"user_id": user_id, "limit": PAGE_SIZE, "offset": offset},
        )
    data = resp.json()
    total = data["total"]
    page = offset // PAGE_SIZE + 1
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

    await query.edit_message_text(
        t(lang, "history_page", total=total, page=page, pages=pages),
        reply_markup=_history_keyboard(data["items"], offset, total, lang),
    )


async def _do_search(update: Update, lang: str, query_text: str) -> None:
    user_id = str(update.effective_user.id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_URL}/search", params={"user_id": user_id, "q": query_text})
    articles = resp.json()

    if not articles:
        await update.message.reply_text(t(lang, "find_empty", query=query_text))
        return

    buttons = []
    for a in articles:
        date = a["created_at"][:10]
        label = f"{a['title'][:35]}… ({date})" if len(a["title"]) > 35 else f"{a['title']} ({date})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"hist:{a['id']}")])

    await update.message.reply_text(
        t(lang, "find_header", query=query_text),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    query_text = " ".join(context.args).strip() if context.args else ""
    if query_text:
        await _do_search(update, lang, query_text)
        return
    context.user_data["awaiting_search"] = True
    await update.message.reply_text(
        t(lang, "find_prompt"),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "link_cancel_btn"), callback_data="findcancel"),
        ]]),
    )


async def handle_history_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    article_id = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/articles/{article_id}",
            params={"user_id": user_id, "lang": lang},
        )

    if resp.status_code == 404:
        await query.edit_message_text(t(lang, "article_not_found"))
        return

    article = resp.json()
    await query.edit_message_text(
        t(lang, "article_ready", title=article["title"]),
        parse_mode="HTML",
        reply_markup=format_keyboard(article_id, lang),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    lang = get_lang(update, context)

    if context.user_data.pop("awaiting_search", False):
        await _do_search(update, lang, text)
        return

    if not is_valid_url(text):
        await update.message.reply_text(t(lang, "not_a_url"))
        return

    status = await update.message.reply_text(t(lang, "status_fetching"))
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or update.effective_user.full_name

    try:
        # Браузерний фолбек (JS/Cloudflare + ретраї) може тривати > 1 хв.
        async with httpx.AsyncClient(timeout=130) as client:
            resp = await client.post(
                f"{API_URL}/extract",
                json={
                    "url": text, "user_id": user_id, "user_type": "telegram",
                    "lang": lang, "username": username,
                },
            )

        if resp.status_code in (400, 403, 408, 502):
            await status.edit_text(f"⚠️ {resp.json().get('detail', 'Error.')}")
            return
        if resp.status_code >= 500:
            await status.edit_text(t(lang, "server_error"))
            return

        article = resp.json()
        await status.edit_text(
            t(lang, "article_ready", title=article["title"]),
            parse_mode="HTML",
            reply_markup=format_keyboard(article["id"], lang),
        )

    except httpx.RequestError:
        await status.edit_text(t(lang, "server_error"))
    except Exception:
        logger.exception("Unexpected error for %s", text)
        await status.edit_text(t(lang, "unexpected_error"))


async def handle_read(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    article_id = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{API_URL}/articles/{article_id}/read",
                params={"user_id": user_id, "lang": lang},
            )
    except httpx.RequestError:
        await query.message.reply_text(t(lang, "server_error"))
        return

    if resp.status_code == 404:
        await query.message.reply_text(t(lang, "article_not_found"))
        return
    if resp.status_code != 200:
        await query.message.reply_text(t(lang, "unexpected_error"))
        return

    chunks = resp.json()["chunks"]
    for i, chunk in enumerate(chunks):
        await query.message.reply_text(
            chunk, parse_mode="HTML", disable_web_page_preview=True,
        )
        if i + 1 < len(chunks):
            await asyncio.sleep(0.4)  # лагідно до Telegram-rate-limit для довгих статей


async def handle_telegraph(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    article_id = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    notice = await query.message.reply_text(t(lang, "status_telegraph"))
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API_URL}/articles/{article_id}/telegraph",
                params={"user_id": user_id, "lang": lang},
            )
    except httpx.RequestError:
        await notice.edit_text(t(lang, "server_error"))
        return

    if resp.status_code == 404:
        await notice.edit_text(t(lang, "article_not_found"))
        return
    if resp.status_code != 200:
        await notice.edit_text(t(lang, "read_telegraph_error"))
        return

    url = resp.json()["url"]
    await notice.edit_text(t(lang, "read_telegraph_ready", url=url))


async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)

    parts = query.data.split(":")
    fmt, article_id = parts[1], int(parts[2])
    user_id = str(query.from_user.id)

    # Окреме статусне повідомлення — картку з кнопками лишаємо, щоб одразу
    # можна було обрати інший формат після завантаження файлу.
    status = await query.message.reply_text(t(lang, "status_generating"))

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(
                f"{API_URL}/articles/{article_id}/download",
                params={"format": fmt, "user_id": user_id, "lang": lang},
            )

        if resp.status_code == 404:
            await status.edit_text(t(lang, "article_not_found"))
            return
        if resp.status_code != 200:
            await status.edit_text(t(lang, "unexpected_error"))
            return

        filename = "article"
        if cd := resp.headers.get("content-disposition"):
            ascii_name = None
            for part in cd.split(";"):
                part = part.strip()
                if part.startswith("filename*="):
                    value = part.split("=", 1)[1]
                    if "''" in value:
                        value = value.split("''", 1)[1]
                    filename = unquote(value)
                    break
                if part.startswith("filename="):
                    ascii_name = part.split("=", 1)[1].strip('"')
            else:
                if ascii_name:
                    filename = ascii_name

        icons = {"pdf": "📄", "md": "📝", "html": "🌐", "epub": "📚"}
        await status.delete()
        await query.message.reply_document(
            document=resp.content,
            filename=filename,
            caption=f"{icons.get(fmt, '')} {filename.rsplit('.', 1)[0].replace('_', ' ')}",
        )

    except Exception:
        logger.exception("Error downloading %s for article %d", fmt, article_id)
        await status.edit_text(t(lang, "unexpected_error"))


# --- Admin --------------------------------------------------------------

ADMIN_PAGE = 8


def _fmt_user_label(u: dict) -> str:
    name = u.get("username") or u["user_id"]
    flag = "🚫 " if u.get("is_banned") else ""
    return f"{flag}{name} · {u['type'][:2]} · {u['article_count']}📄"


def _users_keyboard(items: list, offset: int, total: int) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(_fmt_user_label(u), callback_data=f"usr:{u['user_id']}")] for u in items]
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"usrpage:{offset - ADMIN_PAGE}"))
    if offset + ADMIN_PAGE < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"usrpage:{offset + ADMIN_PAGE}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(buttons)


async def _fetch_users(user_id: str, offset: int) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/admin/users",
            params={"admin_id": user_id, "limit": ADMIN_PAGE, "offset": offset},
        )
    return resp.json()


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_denied"))
        return
    await update.message.reply_text(t(lang, "admin_menu"), parse_mode="HTML")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_denied"))
        return
    user_id = str(update.effective_user.id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_URL}/admin/stats", params={"admin_id": user_id})
    s = resp.json()
    await update.message.reply_text(
        t(lang, "admin_stats", **s),
        parse_mode="HTML",
    )


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_denied"))
        return
    data = await _fetch_users(str(update.effective_user.id), 0)
    await update.message.reply_text(
        t(lang, "admin_users_header", total=data["total"]),
        reply_markup=_users_keyboard(data["items"], 0, data["total"]),
    )


async def handle_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    if not is_admin(query.from_user.id):
        return
    offset = int(query.data.split(":")[1])
    data = await _fetch_users(str(query.from_user.id), offset)
    await query.edit_message_text(
        t(lang, "admin_users_header", total=data["total"]),
        reply_markup=_users_keyboard(data["items"], offset, data["total"]),
    )


async def handle_user_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    if not is_admin(query.from_user.id):
        return
    target_id = query.data.split(":", 1)[1]

    data = await _fetch_users(str(query.from_user.id), 0)
    # Знаходимо юзера в повному списку (через всі сторінки за потреби).
    user = next((u for u in data["items"] if u["user_id"] == target_id), None)
    offset = ADMIN_PAGE
    while user is None and offset < data["total"]:
        more = await _fetch_users(str(query.from_user.id), offset)
        user = next((u for u in more["items"] if u["user_id"] == target_id), None)
        offset += ADMIN_PAGE

    if user is None:
        await query.edit_message_text(t(lang, "admin_user_gone"))
        return

    name = user.get("username") or "—"
    banned = bool(user.get("is_banned"))
    text = t(
        lang, "admin_user_card",
        name=name, user_id=user["user_id"], type=user["type"],
        articles=user["article_count"], ulang=user["lang"],
        created=(user["created_at"] or "—")[:10],
        active=(user.get("last_active_at") or "—")[:10],
        status=t(lang, "admin_status_banned" if banned else "admin_status_active"),
    )
    action = ("unban", "admin_unban_btn") if banned else ("ban", "admin_ban_btn")
    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "admin_articles_btn"), callback_data=f"uarts:{target_id}:0")],
            [
                InlineKeyboardButton(t(lang, action[1]), callback_data=f"{action[0]}:{target_id}"),
                InlineKeyboardButton(t(lang, "admin_back_btn"), callback_data="usrpage:0"),
            ],
        ]),
    )


async def handle_user_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    if not is_admin(query.from_user.id):
        return
    action, target_id = query.data.split(":", 1)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/admin/users/{target_id}/{action}",
            params={"admin_id": str(query.from_user.id)},
        )
    key = "admin_banned_done" if action == "ban" else "admin_unbanned_done"
    if resp.status_code != 200:
        key = "admin_user_gone"
    await query.edit_message_text(
        t(lang, key, user_id=target_id),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "admin_back_btn"), callback_data="usrpage:0"),
        ]]),
    )


def _failed_keyboard(offset: int, total: int) -> InlineKeyboardMarkup | None:
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"failedpage:{offset - ADMIN_PAGE}"))
    if offset + ADMIN_PAGE < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"failedpage:{offset + ADMIN_PAGE}"))
    return InlineKeyboardMarkup([nav]) if nav else None


def _failed_text(lang: str, data: dict) -> str:
    if not data["items"]:
        return t(lang, "admin_failed_empty")
    lines = [t(lang, "admin_failed_header", total=data["total"])]
    for f in data["items"]:
        lines.append(f"• <code>{f['error']}</code> — {f['url'][:60]}\n  <i>{f['created_at'][:16]}</i>")
    return "\n".join(lines)


async def failed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_denied"))
        return
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/admin/failed",
            params={"admin_id": str(update.effective_user.id), "limit": ADMIN_PAGE, "offset": 0},
        )
    data = resp.json()
    await update.message.reply_text(
        _failed_text(lang, data), parse_mode="HTML",
        reply_markup=_failed_keyboard(0, data["total"]),
        disable_web_page_preview=True,
    )


async def handle_failed_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    if not is_admin(query.from_user.id):
        return
    offset = int(query.data.split(":")[1])
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/admin/failed",
            params={"admin_id": str(query.from_user.id), "limit": ADMIN_PAGE, "offset": offset},
        )
    data = resp.json()
    await query.edit_message_text(
        _failed_text(lang, data), parse_mode="HTML",
        reply_markup=_failed_keyboard(offset, data["total"]),
        disable_web_page_preview=True,
    )


# --- Admin: статті користувача -----------------------------------------

def _user_articles_keyboard(user_id: str, items: list, offset: int, total: int, lang: str) -> InlineKeyboardMarkup:
    buttons = []
    for a in items:
        date = a["created_at"][:10]
        label = f"{a['title'][:35]}… ({date})" if len(a["title"]) > 35 else f"{a['title']} ({date})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"uart:{user_id}:{a['id']}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"uarts:{user_id}:{offset - ADMIN_PAGE}"))
    if offset + ADMIN_PAGE < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"uarts:{user_id}:{offset + ADMIN_PAGE}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(t(lang, "admin_back_users_btn"), callback_data="usrpage:0")])
    return InlineKeyboardMarkup(buttons)


async def handle_user_articles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    if not is_admin(query.from_user.id):
        return
    _, target_id, offset = query.data.split(":")
    offset = int(offset)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/admin/users/{target_id}/articles",
            params={"admin_id": str(query.from_user.id), "limit": ADMIN_PAGE, "offset": offset},
        )
    data = resp.json()

    if not data["items"]:
        await query.edit_message_text(
            t(lang, "admin_user_articles_empty"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t(lang, "admin_back_users_btn"), callback_data="usrpage:0"),
            ]]),
        )
        return

    await query.edit_message_text(
        t(lang, "admin_user_articles_header", total=data["total"]),
        reply_markup=_user_articles_keyboard(target_id, data["items"], offset, data["total"], lang),
    )


async def handle_admin_article_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    if not is_admin(query.from_user.id):
        return
    _, target_id, article_id = query.data.split(":")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/admin/articles/{article_id}",
            params={"admin_id": str(query.from_user.id)},
        )
    if resp.status_code != 200:
        await query.edit_message_text(t(lang, "article_not_found"))
        return
    article = resp.json()

    await query.edit_message_text(
        t(lang, "admin_article_card", title=article["title"], url=article["url"], created=article["created_at"][:10]),
        parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(t(lang, "format_pdf"), callback_data=f"afmt:pdf:{article_id}"),
                InlineKeyboardButton(t(lang, "format_md"), callback_data=f"afmt:md:{article_id}"),
            ],
            [
                InlineKeyboardButton(t(lang, "format_html"), callback_data=f"afmt:html:{article_id}"),
                InlineKeyboardButton(t(lang, "format_epub"), callback_data=f"afmt:epub:{article_id}"),
            ],
            [InlineKeyboardButton(t(lang, "format_delete"), callback_data=f"adel:{target_id}:{article_id}")],
            [InlineKeyboardButton(t(lang, "admin_back_articles_btn"), callback_data=f"uarts:{target_id}:0")],
        ]),
    )


async def handle_admin_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    if not is_admin(query.from_user.id):
        return
    _, fmt, article_id = query.data.split(":")

    status = await query.message.reply_text(t(lang, "status_generating"))
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(
                f"{API_URL}/admin/articles/{article_id}/download",
                params={"format": fmt, "admin_id": str(query.from_user.id), "lang": lang},
            )
        if resp.status_code == 404:
            await status.edit_text(t(lang, "article_not_found"))
            return
        if resp.status_code != 200:
            await status.edit_text(t(lang, "unexpected_error"))
            return

        filename = "article"
        if cd := resp.headers.get("content-disposition"):
            ascii_name = None
            for part in cd.split(";"):
                part = part.strip()
                if part.startswith("filename*="):
                    value = part.split("=", 1)[1]
                    if "''" in value:
                        value = value.split("''", 1)[1]
                    filename = unquote(value)
                    break
                if part.startswith("filename="):
                    ascii_name = part.split("=", 1)[1].strip('"')
            else:
                if ascii_name:
                    filename = ascii_name

        icons = {"pdf": "📄", "md": "📝", "html": "🌐", "epub": "📚"}
        await status.delete()
        await query.message.reply_document(
            document=resp.content,
            filename=filename,
            caption=f"{icons.get(fmt, '')} {filename.rsplit('.', 1)[0].replace('_', ' ')}",
        )
    except Exception:
        logger.exception("Admin error downloading %s for article %s", fmt, article_id)
        await status.edit_text(t(lang, "unexpected_error"))


async def handle_admin_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    if not is_admin(query.from_user.id):
        return
    _, target_id, article_id = query.data.split(":")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/admin/articles/{article_id}",
            params={"admin_id": str(query.from_user.id)},
        )
    title = resp.json().get("title", "?") if resp.status_code == 200 else "?"
    await query.edit_message_text(
        t(lang, "admin_article_delete_confirm", title=title),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "delete_confirm_btn"), callback_data=f"adelok:{target_id}:{article_id}"),
            InlineKeyboardButton(t(lang, "link_cancel_btn"), callback_data=f"uart:{target_id}:{article_id}"),
        ]]),
    )


async def handle_admin_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    if not is_admin(query.from_user.id):
        return
    _, target_id, article_id = query.data.split(":")

    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{API_URL}/admin/articles/{article_id}",
            params={"admin_id": str(query.from_user.id)},
        )
    key = "admin_article_deleted" if resp.status_code == 200 else "article_not_found"
    await query.edit_message_text(
        t(lang, key),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "admin_back_articles_btn"), callback_data=f"uarts:{target_id}:0"),
        ]]),
    )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    # concurrent_updates: апдейти обробляються паралельно, інакше довгий /extract
    # одного юзера блокує диспетчер для всіх інших.
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(MessageHandler(filters.COMMAND, _clear_awaiting_states), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("link", link))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("failed", failed_cmd))
    app.add_handler(CallbackQueryHandler(handle_history_page, pattern=r"^histpage:"))
    app.add_handler(CallbackQueryHandler(handle_history_select, pattern=r"^hist:"))
    app.add_handler(CallbackQueryHandler(handle_read, pattern=r"^read:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_telegraph, pattern=r"^tgph:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_format, pattern=r"^fmt:"))
    app.add_handler(CallbackQueryHandler(handle_delete, pattern=r"^del:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_delete_confirm, pattern=r"^delconfirm:"))
    app.add_handler(CallbackQueryHandler(handle_delete_cancel, pattern=r"^delcancel:"))
    app.add_handler(CallbackQueryHandler(handle_share_generate, pattern=r"^share:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_share_revoke, pattern=r"^sharerevoke:"))
    app.add_handler(CallbackQueryHandler(handle_link_callback, pattern=r"^link"))
    app.add_handler(CallbackQueryHandler(handle_findcancel, pattern=r"^findcancel$"))
    app.add_handler(CallbackQueryHandler(handle_setlang, pattern=r"^setlang:"))
    app.add_handler(CallbackQueryHandler(handle_users_page, pattern=r"^usrpage:"))
    app.add_handler(CallbackQueryHandler(handle_user_select, pattern=r"^usr:"))
    app.add_handler(CallbackQueryHandler(handle_user_ban, pattern=r"^(ban|unban):"))
    app.add_handler(CallbackQueryHandler(handle_failed_page, pattern=r"^failedpage:"))
    app.add_handler(CallbackQueryHandler(handle_user_articles, pattern=r"^uarts:"))
    app.add_handler(CallbackQueryHandler(handle_admin_article_select, pattern=r"^uart:"))
    app.add_handler(CallbackQueryHandler(handle_admin_format, pattern=r"^afmt:"))
    app.add_handler(CallbackQueryHandler(handle_admin_delete_confirm, pattern=r"^adelok:"))
    app.add_handler(CallbackQueryHandler(handle_admin_delete, pattern=r"^adel:"))
    app.add_handler(MessageHandler(filters.Regex(r"^[A-Z0-9]{6}$"), handle_link_code))
    app.add_handler(MessageHandler(filters.Regex(r"^[A-Z0-9]{8}$"), handle_share_code))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
