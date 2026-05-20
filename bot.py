import logging
import os
import re
import tempfile
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from readability import Document
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from weasyprint import HTML, CSS

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

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
    overflow-x: auto;
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


def is_valid_url(text: str) -> bool:
    try:
        parsed = urlparse(text)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def safe_filename(title: str, max_len: int = 60) -> str:
    name = re.sub(r'[^\w\s\-]', '', title, flags=re.UNICODE)
    name = re.sub(r'\s+', '_', name.strip())
    return (name[:max_len] or "article") + ".pdf"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привіт! Надішли мені посилання на статтю — я прочитаю її в режимі читача "
        "(без реклами, банерів і коментарів) та поверну чистий PDF.\n\n"
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

    pdf_path = None
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

        await status.edit_text("🖨 Генерую PDF…")

        page_html = f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<title>{title}</title>
</head>
<body>
<h1>{title}</h1>
<p class="source">Джерело: <a href="{text}">{text}</a></p>
{content_html}
</body>
</html>"""

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = tmp.name

        HTML(string=page_html, base_url=text).write_pdf(
            pdf_path,
            stylesheets=[READER_CSS],
            presentational_hints=True,
        )

        await status.delete()
        with open(pdf_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=safe_filename(title),
                caption=f"📄 {title}",
            )

    except httpx.TimeoutException:
        await status.edit_text("❌ Час очікування вичерпано. Сайт відповідає надто повільно.")
    except httpx.HTTPStatusError as e:
        await status.edit_text(f"❌ Сервер повернув помилку: {e.response.status_code}.")
    except httpx.RequestError as e:
        await status.edit_text(f"❌ Не вдалося підключитися до сайту: {e}")
    except Exception:
        logger.exception("Unexpected error while processing %s", text)
        await status.edit_text("❌ Сталася непередбачена помилка. Спробуй ще раз.")
    finally:
        if pdf_path:
            try:
                os.unlink(pdf_path)
            except Exception:
                pass


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
