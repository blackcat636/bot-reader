import logging
import os
from urllib.parse import urlparse

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


async def find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update, context)
    query_text = " ".join(context.args).strip() if context.args else ""
    if not query_text:
        await update.message.reply_text(t(lang, "find_usage"), parse_mode="HTML")
        return

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

    if not is_valid_url(text):
        await update.message.reply_text(t(lang, "not_a_url"))
        return

    status = await update.message.reply_text(t(lang, "status_fetching"))
    user_id = str(update.effective_user.id)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API_URL}/extract",
                json={"url": text, "user_id": user_id, "user_type": "telegram", "lang": lang},
            )

        if resp.status_code in (400, 408, 502):
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


async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)

    parts = query.data.split(":")
    fmt, article_id = parts[1], int(parts[2])
    user_id = str(query.from_user.id)

    await query.edit_message_text(t(lang, "status_generating"))

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(
                f"{API_URL}/articles/{article_id}/download",
                params={"format": fmt, "user_id": user_id, "lang": lang},
            )

        if resp.status_code == 404:
            await query.edit_message_text(t(lang, "article_not_found"))
            return
        if resp.status_code != 200:
            await query.edit_message_text(t(lang, "unexpected_error"))
            return

        filename = "article"
        if cd := resp.headers.get("content-disposition"):
            for part in cd.split(";"):
                part = part.strip()
                if part.startswith("filename="):
                    filename = part.split("=", 1)[1].strip('"')

        icons = {"pdf": "📄", "md": "📝", "html": "🌐", "epub": "📚"}
        await query.delete_message()
        await query.message.reply_document(
            document=resp.content,
            filename=filename,
            caption=f"{icons.get(fmt, '')} {filename.rsplit('.', 1)[0].replace('_', ' ')}",
        )

    except Exception:
        logger.exception("Error downloading %s for article %d", fmt, article_id)
        await query.edit_message_text(t(lang, "unexpected_error"))


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("link", link))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CallbackQueryHandler(handle_history_page, pattern=r"^histpage:"))
    app.add_handler(CallbackQueryHandler(handle_history_select, pattern=r"^hist:"))
    app.add_handler(CallbackQueryHandler(handle_format, pattern=r"^fmt:"))
    app.add_handler(CallbackQueryHandler(handle_share_generate, pattern=r"^share:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_share_revoke, pattern=r"^sharerevoke:"))
    app.add_handler(CallbackQueryHandler(handle_link_callback, pattern=r"^link"))
    app.add_handler(CallbackQueryHandler(handle_setlang, pattern=r"^setlang:"))
    app.add_handler(MessageHandler(filters.Regex(r"^[A-Z0-9]{6}$"), handle_link_code))
    app.add_handler(MessageHandler(filters.Regex(r"^[A-Z0-9]{8}$"), handle_share_code))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
