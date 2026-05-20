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

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_URL = os.getenv("API_URL", "http://api:8000")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def format_keyboard(article_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF", callback_data=f"fmt:pdf:{article_id}"),
            InlineKeyboardButton("📝 Markdown", callback_data=f"fmt:md:{article_id}"),
        ],
        [
            InlineKeyboardButton("🌐 HTML", callback_data=f"fmt:html:{article_id}"),
            InlineKeyboardButton("📚 EPUB", callback_data=f"fmt:epub:{article_id}"),
        ],
        [
            InlineKeyboardButton("📤 Поділитись", callback_data=f"share:{article_id}"),
        ],
    ])


def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привіт! Надішли мені посилання на статтю — я завантажу її в режимі читача "
        "(без реклами, банерів і коментарів) і запитаю, у якому форматі зберегти.\n\n"
        "Просто вставте URL і надішліть.\n"
        "/history — переглянути збережені статті.\n"
        "/link — прив'язати Chrome розширення."
    )


async def link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/link/generate",
            params={"user_id": user_id, "user_type": "telegram"},
        )
    if resp.status_code != 200:
        await update.message.reply_text("❌ Не вдалося згенерувати код. Спробуй ще раз.")
        return
    code = resp.json()["code"]
    await update.message.reply_text(
        f"🔗 Код для прив'язки розширення:\n\n"
        f"<code>{code}</code>\n\n"
        f"Введи його в налаштуваннях Chrome розширення. Діє 10 хвилин.\n\n"
        f"Або надішли мені 6-символьний код з розширення, щоб прив'язати його до свого акаунту.",
        parse_mode="HTML",
    )


async def handle_link_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = update.message.text.strip().upper()
    user_id = str(update.effective_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/link/preview",
            params={"code": code, "user_id": user_id, "user_type": "telegram"},
        )

    if resp.status_code != 200:
        await update.message.reply_text(f"❌ {resp.json().get('detail', 'Помилка.')}")
        return

    data = resp.json()

    if data.get("already_same_group"):
        await update.message.reply_text("ℹ️ Ці акаунти вже в одній групі.")
        return

    text = (
        f"🔗 <b>Попередній перегляд злиття</b>\n\n"
        f"Твоя група: {data['your_group_users']} акаунт(ів), {data['your_group_articles']} статей\n"
        f"Інша група: {data['code_group_users']} акаунт(ів), {data['code_group_articles']} статей\n\n"
        f"Після злиття: <b>{data['total_users']} акаунти, {data['total_articles']} статей</b>\n\n"
        f"Всі статті стануть спільними. Продовжити?"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Так, злити", callback_data=f"linkconfirm:{code}"),
        InlineKeyboardButton("❌ Скасувати", callback_data="linkcancel"),
    ]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def handle_share_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    article_id = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/share/generate",
            params={"article_id": article_id, "user_id": user_id},
        )

    if resp.status_code != 200:
        await query.answer("❌ Не вдалося згенерувати код.", show_alert=True)
        return

    code = resp.json()["code"]
    await query.message.reply_text(
        f"📤 Код для передачі статті:\n\n"
        f"<code>{code}</code>\n\n"
        f"Надішли його іншому користувачу. Код одноразовий — діє до використання.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Відмінити код", callback_data=f"sharerevoke:{code}:{user_id}"),
        ]]),
    )


async def handle_share_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    code, user_id = parts[1], parts[2]

    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{API_URL}/share/{code}",
            params={"user_id": user_id},
        )

    if resp.status_code == 200:
        await query.edit_message_text("✅ Код відмінено.")
    else:
        await query.edit_message_text("❌ Код вже використано або не знайдено.")


async def handle_share_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = update.message.text.strip().upper()
    user_id = str(update.effective_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/share/claim",
            json={"code": code, "user_id": user_id, "user_type": "telegram"},
        )

    if resp.status_code != 200:
        await update.message.reply_text(f"❌ {resp.json().get('detail', 'Помилка.')}")
        return

    article = resp.json()
    await update.message.reply_text(
        f"✅ Стаття додана до твоєї історії!\n\n"
        f"<b>{article['title']}</b>\n\nОберіть формат:",
        parse_mode="HTML",
        reply_markup=format_keyboard(article["id"]),
    )


async def handle_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "linkcancel":
        await query.edit_message_text("Скасовано.")
        return

    code = query.data.split(":")[1]
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/link/confirm",
            json={"code": code, "user_id": user_id, "user_type": "telegram"},
        )

    if resp.status_code != 200:
        await query.edit_message_text(f"❌ {resp.json().get('detail', 'Помилка.')}")
        return

    merged = resp.json()["merged_users"]
    await query.edit_message_text(f"✅ Готово! Акаунтів у групі: {merged}.")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_URL}/history", params={"user_id": user_id})
    articles = resp.json()

    if not articles:
        await update.message.reply_text("Історія порожня. Надішли посилання на статтю — я збережу її.")
        return

    buttons = []
    for a in articles:
        date = a["created_at"][:10]
        label = f"{a['title'][:35]}… ({date})" if len(a["title"]) > 35 else f"{a['title']} ({date})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"hist:{a['id']}")])

    await update.message.reply_text(
        "Останні збережені статті:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_history_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    article_id = int(query.data.split(":")[1])
    user_id = str(query.from_user.id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_URL}/articles/{article_id}/download",
            params={"format": "pdf", "user_id": user_id},
        )

    if resp.status_code == 404:
        await query.edit_message_text("❌ Стаття не знайдена.")
        return

    await query.edit_message_text(
        f"✅ Оберіть формат:",
        reply_markup=format_keyboard(article_id),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()

    if not is_valid_url(text):
        await update.message.reply_text(
            "Це не схоже на посилання. Надішли URL, що починається з http:// або https://"
        )
        return

    status = await update.message.reply_text("⏳ Завантажую сторінку…")
    user_id = str(update.effective_user.id)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API_URL}/extract",
                json={"url": text, "user_id": user_id, "user_type": "telegram"},
            )

        if resp.status_code == 400:
            await status.edit_text(f"⚠️ {resp.json()['detail']}")
            return
        if resp.status_code == 408:
            await status.edit_text("❌ Час очікування вичерпано. Сайт відповідає надто повільно.")
            return
        if resp.status_code >= 500:
            await status.edit_text(f"❌ {resp.json().get('detail', 'Помилка сервера.')}")
            return

        article = resp.json()
        await status.edit_text(
            f"✅ <b>{article['title']}</b>\n\nОберіть формат:",
            parse_mode="HTML",
            reply_markup=format_keyboard(article["id"]),
        )

    except httpx.RequestError:
        await status.edit_text("❌ Не вдалося зв'язатися з сервером. Спробуй ще раз.")
    except Exception:
        logger.exception("Unexpected error for %s", text)
        await status.edit_text("❌ Сталася непередбачена помилка. Спробуй ще раз.")


async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    fmt, article_id = parts[1], int(parts[2])
    user_id = str(query.from_user.id)

    await query.edit_message_text("⏳ Генерую файл…")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(
                f"{API_URL}/articles/{article_id}/download",
                params={"format": fmt, "user_id": user_id},
            )

        if resp.status_code == 404:
            await query.edit_message_text("❌ Стаття не знайдена. Надішли посилання ще раз.")
            return
        if resp.status_code != 200:
            await query.edit_message_text("❌ Помилка генерації файлу.")
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
        await query.edit_message_text("❌ Сталася непередбачена помилка. Спробуй ще раз.")


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("link", link))
    app.add_handler(CallbackQueryHandler(handle_history_select, pattern=r"^hist:"))
    app.add_handler(CallbackQueryHandler(handle_format, pattern=r"^fmt:"))
    app.add_handler(CallbackQueryHandler(handle_share_generate, pattern=r"^share:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_share_revoke, pattern=r"^sharerevoke:"))
    app.add_handler(CallbackQueryHandler(handle_link_callback, pattern=r"^link"))
    app.add_handler(MessageHandler(filters.Regex(r"^[A-Z0-9]{6}$"), handle_link_code))
    app.add_handler(MessageHandler(filters.Regex(r"^[A-Z0-9]{8}$"), handle_share_code))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
