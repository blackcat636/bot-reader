import re

import httpx
from readability import Document

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk,en-US;q=0.9,en;q=0.8",
}


class ExtractError(Exception):
    pass


async def fetch_and_extract(url: str) -> dict:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=30,
        headers=FETCH_HEADERS,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        raise ExtractError("not_html")

    doc = Document(response.text)
    title = doc.title() or "Стаття"
    content_html = doc.summary(html_partial=True)

    if not content_html or len(re.sub(r'<[^>]+>', '', content_html).strip()) < 200:
        raise ExtractError("no_content")

    return {"title": title, "content_html": content_html}
