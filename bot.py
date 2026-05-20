import logging
import os
import re
import tempfile
from urllib.parse import urlparse

import html2text
import httpx
from ebooklib import epub
from dotenv import load_dotenv
from readability import Document
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from weasyprint import HTML, CSS

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

READER_CSS_STRING = """
body {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 15px;
    line-height: 1.7;
    color: #1a1a1a;
    max-width: 720px;
    margin: 40px auto;
    padding: 0 20px;
}
h1 {
    font-size: 26px;
    font-weight: bold;
    line-height: 1.3;
    margin: 0 0 8px 0;
    color: #111;
}
h2 { font-size: 20px; margin-top: 32px; margin-bottom: 8px; color: #222; }
h3 { font-size: 17px; margin-top: 24px; margin-bottom: 6px; color: #333; }
.source {
    font-size: 12px;
    color: #777;
    margin-bottom: 28px;
    padding-bottom: 16px;
    border-bottom: 1px solid #ddd;
    word-break: break-all;
}
.source a { color: #555; }
p { margin: 0 0 14px 0; }
img { max-width: 100%; height: auto; display: block; margin: 16px auto; }
a { color: #1a5fa8; }
blockquote {
    border-left: 3px solid #ccc;
    margin: 16px 0;
    padding: 4px 0 4px 16px;
    color: #555;
    font-style: italic;
}
pre {
    background: #f5f5f5;
    padding: 12px;
    border-radius: 4px;
    font-size: 13px;
    white-space: pre-wrap;
    word-break: break-all;
}
code {
    font-family: "Courier New", monospace;
    font-size: 13px;
    background: #f5f5f5;
    padding: 1px 4px;
    border-radius: 2px;
}
ul, ol { margin: 0 0 14px 0; padding-left: 24px; }
li { margin-bottom: 4px; }
figure { margin: 16px 0; text-align: center; }
figcaption { font-size: 12px; color: #777; margin-top: 6px; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }
th { background: #f0f0f0; font-weight: bold; }
"""

READER_CSS = CSS(string="""
@page {
    margin: 2.5cm 2cm;
    size: A4;
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-size: 10px;
        color: #888;
    }
}
body {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 13px;
    line-height: 1.7;
    color: #1a1a1a;
}
h1 {
    font-size: 22px;
    font-weight: bold;
    line-height: 1.3;
    margin: 0 0 8px 0;
    color: #111;
}
h2 { font-size: 18px; margin-top: 28px; margin-bottom: 8px; color: #222; }
h3 { font-size: 15px; margin-top: 20px; margin-bottom: 6px; color: #333; }
.source {
    font-size: 11px;
    color: #777;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid #ddd;
    word-break: break-all;
}
.source a { color: #555; }
p { margin: 0 0 12px 0; }
img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 16px auto;
}
a { color: #1a5fa8; }
blockquote {
    border-left: 3px solid #ccc;
    margin: 16px 0;
    padding: 4px 0 4px 16px;
    color: #555;
    font-style: italic;
}
pre {
    background: #f5f5f5;
    padding: 12px;
    border-radius: 4px;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-all;
}
code {
    font-family: "Courier New", monospace;
    font-size: 11px;
    background: #f5f5f5;
    padding: 1px 4px;
    border-radius: 2px;
}
ul, ol { margin: 0 0 12px 0; padding-left: 24px; }
li { margin-bottom: 4px; }
figure { margin: 16px 0; text-align: center; }
figcaption { font-size: 11px; color: #777; margin-top: 6px; }
table {
    border-collapse: collapse;
    width: 100%;
    margin: 16px 0;
    font-size: 12px;
}
th, td {
    border: 1px solid #ddd;
    padding: 6px 10px;
    text-align: left;
}
th { background: #f0f0f0; font-weight: bold; }
""")

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk,en-US;q=0.9,en;q=0.8",
}

FORMAT_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📄 PDF", callback_data="fmt:pdf"),
        InlineKeyboardButton("📝 Markdown", callback_data="fmt:md"),
    ],
    [
        InlineKeyboardButton("🌐 HTML", callback_data="fmt:html"),
        InlineKeyboardButton("📚 EPUB", callback_data="fmt:epub"),
    ],
])


def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def safe_filename(title: str, ext: str, max_len: int = 60) -> str:
    name = re.sub(r'[^\w\s\-]', '', title, flags=re.UNICODE)
    name = re.sub(r'\s+', '_', name.strip())
    return (name[:max_len] or "article") + ext


def build_page_html(title: str, url: str, content_html: str, inline_css: str = "") -> str:
    style_block = f"<style>{inline_css}</style>" if inline_css else ""
    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<title>{title}</title>
{style_block}
</head>
<body>
<h1>{title}</h1>
<p class="source">Джерело: <a href="{url}">{url}</a></p>
{content_html}
</body>
</html>"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привіт! Надішли мені посилання на статтю — я завантажу її в режимі читача "
        "(без реклами, банерів і коментарів) і запитаю, у якому форматі зберегти.\n\n"
        "Просто вставте URL і надішліть."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()

    if not is_valid_url(text):
        await update.message.reply_text(
            "Це не схоже на посилання. Надішли URL, що починається з http:// або https://"
        )
        return

    status = await update.message.reply_text("⏳ Завантажую сторінку…")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers=FETCH_HEADERS,
        ) as client:
            response = await client.get(text)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            await status.edit_text("❌ Посилання веде не на HTML-сторінку (можливо, це зображення або файл).")
            return

        await status.edit_text("📖 Виділяю текст статті…")

        doc = Document(response.text)
        title = doc.title() or "Стаття"
        content_html = doc.summary(html_partial=True)

        if not content_html or len(re.sub(r'<[^>]+>', '', content_html).strip()) < 200:
            await status.edit_text(
                "⚠️ Не вдалося виділити текст статті — можливо, сторінка захищена або "
                "потребує авторизації."
            )
            return

        context.user_data["article"] = {
            "url": text,
            "title": title,
            "content_html": content_html,
        }

        await status.edit_text(
            f"✅ <b>{title}</b>\n\nОберіть формат:",
            parse_mode="HTML",
            reply_markup=FORMAT_KEYBOARD,
        )

    except httpx.TimeoutException:
        await status.edit_text("❌ Час очікування вичерпано. Сайт відповідає надто повільно.")
    except httpx.HTTPStatusError as e:
        await status.edit_text(f"❌ Сервер повернув помилку: {e.response.status_code}.")
    except httpx.RequestError as e:
        await status.edit_text(f"❌ Не вдалося підключитися до сайту: {e}")
    except Exception:
        logger.exception("Unexpected error while fetching %s", text)
        await status.edit_text("❌ Сталася непередбачена помилка. Спробуй ще раз.")


async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    article = context.user_data.get("article")
    if not article:
        await query.edit_message_text("❌ Сесія застаріла. Надішли посилання ще раз.")
        return

    fmt = query.data.split(":")[1]
    url = article["url"]
    title = article["title"]
    content_html = article["content_html"]

    await query.edit_message_text("⏳ Генерую файл…")

    tmp_path = None
    try:
        if fmt == "pdf":
            page_html = build_page_html(title, url, content_html)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            HTML(string=page_html, base_url=url).write_pdf(
                tmp_path,
                stylesheets=[READER_CSS],
                presentational_hints=True,
            )
            filename = safe_filename(title, ".pdf")
            caption = f"📄 {title}"

        elif fmt == "md":
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.body_width = 0
            md = f"# {title}\n\nДжерело: {url}\n\n" + h.handle(content_html)
            with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as tmp:
                tmp_path = tmp.name
                tmp.write(md)
            filename = safe_filename(title, ".md")
            caption = f"📝 {title}"

        elif fmt == "epub":
            book = epub.EpubBook()
            book.set_identifier(url)
            book.set_title(title)
            book.set_language("uk")

            chapter = epub.EpubHtml(title=title, file_name="article.xhtml", lang="uk")
            chapter.content = build_page_html(title, url, content_html)
            book.add_item(chapter)
            book.toc = [chapter]
            book.spine = ["nav", chapter]
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())

            with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
                tmp_path = tmp.name
            epub.write_epub(tmp_path, book)
            filename = safe_filename(title, ".epub")
            caption = f"📚 {title}"

        else:  # html
            page_html = build_page_html(title, url, content_html, inline_css=READER_CSS_STRING)
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as tmp:
                tmp_path = tmp.name
                tmp.write(page_html)
            filename = safe_filename(title, ".html")
            caption = f"🌐 {title}"

        await query.delete_message()
        with open(tmp_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=filename,
                caption=caption,
            )

    except Exception:
        logger.exception("Unexpected error while generating %s for %s", fmt, url)
        await query.edit_message_text("❌ Сталася непередбачена помилка. Спробуй ще раз.")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_format, pattern=r"^fmt:"))

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
