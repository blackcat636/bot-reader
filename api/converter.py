import re
import tempfile
import os

import html2text
from ebooklib import epub
from weasyprint import HTML, CSS

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
h1 { font-size: 22px; font-weight: bold; line-height: 1.3; margin: 0 0 8px 0; color: #111; }
h2 { font-size: 18px; margin-top: 28px; margin-bottom: 8px; color: #222; }
h3 { font-size: 15px; margin-top: 20px; margin-bottom: 6px; color: #333; }
.source {
    font-size: 11px; color: #777; margin-bottom: 24px;
    padding-bottom: 16px; border-bottom: 1px solid #ddd; word-break: break-all;
}
.source a { color: #555; }
p { margin: 0 0 12px 0; }
img { max-width: 100%; height: auto; display: block; margin: 16px auto; }
a { color: #1a5fa8; }
blockquote {
    border-left: 3px solid #ccc; margin: 16px 0;
    padding: 4px 0 4px 16px; color: #555; font-style: italic;
}
pre {
    background: #f5f5f5; padding: 12px; border-radius: 4px;
    font-size: 11px; white-space: pre-wrap; word-break: break-all;
}
code {
    font-family: "Courier New", monospace; font-size: 11px;
    background: #f5f5f5; padding: 1px 4px; border-radius: 2px;
}
ul, ol { margin: 0 0 12px 0; padding-left: 24px; }
li { margin-bottom: 4px; }
figure { margin: 16px 0; text-align: center; }
figcaption { font-size: 11px; color: #777; margin-top: 6px; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 12px; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }
th { background: #f0f0f0; font-weight: bold; }
""")

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "md": "text/markdown",
    "html": "text/html",
    "epub": "application/epub+zip",
}

EXTENSIONS = {
    "pdf": ".pdf",
    "md": ".md",
    "html": ".html",
    "epub": ".epub",
}


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


async def generate_file(title: str, url: str, content_html: str, fmt: str) -> tuple[bytes, str]:
    tmp_path = None
    try:
        if fmt == "pdf":
            page_html = build_page_html(title, url, content_html)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            HTML(string=page_html, base_url=url).write_pdf(
                tmp_path, stylesheets=[READER_CSS], presentational_hints=True,
            )
            with open(tmp_path, "rb") as f:
                data = f.read()

        elif fmt == "md":
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.body_width = 0
            md = f"# {title}\n\nДжерело: {url}\n\n" + h.handle(content_html)
            data = md.encode("utf-8")

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
            with open(tmp_path, "rb") as f:
                data = f.read()

        else:  # html
            page_html = build_page_html(title, url, content_html, inline_css=READER_CSS_STRING)
            data = page_html.encode("utf-8")

        return data, safe_filename(title, EXTENSIONS[fmt])

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
