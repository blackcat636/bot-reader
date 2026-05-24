"""Конвертація збереженого content_html у два формати для читання в Telegram:

- html_to_chunks       → Telegram-підмножина HTML, порізана на повідомлення ≤ ліміту
                         (режим «Читати тут», прямо в чаті).
- html_to_telegraph_nodes → масив вузлів Telegraph (telegra.ph), що Telegram
                         відкриває нативним Instant View.

Обидві функції синхронні й CPU-bound (lxml-парсинг) — main.py викликає їх через
asyncio.to_thread, як і converter.generate_file.
"""
import html as _html
import re
from urllib.parse import urljoin

import lxml.html

# --- Спільне ------------------------------------------------------------

# Елементи, що несуть лише шум/неконвертований контент — викидаємо повністю.
_DROP_TAGS = {"script", "style", "noscript", "iframe", "form", "button", "svg"}


def _parse(content_html: str):
    """Сирий фрагмент content_html → кореневий <div> lxml (стійко до кількох top-level вузлів)."""
    try:
        return lxml.html.fragment_fromstring(content_html, create_parent="div")
    except Exception:
        return lxml.html.fromstring(f"<div>{content_html}</div>")


# --- Режим «Читати тут»: Telegram-HTML, порізаний на повідомлення -------

# Telegram приймає лише цей набір inline-тегів (parse_mode=HTML).
_TG_INLINE = {
    "b": "b", "strong": "b",
    "i": "i", "em": "i",
    "u": "u", "ins": "u",
    "s": "s", "strike": "s", "del": "s",
    "code": "code",
}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# Telegram-ліміт 4096; лишаємо запас на заголовок/обгортки.
_TG_LIMIT = 3800


def _esc(text: str) -> str:
    return _html.escape(text or "", quote=False)


def _esc_attr(text: str) -> str:
    return _html.escape(text or "", quote=True)


def _render_inline(el) -> str:
    """Inline-вміст елемента у Telegram-HTML (рекурсивно). Блокові діти теж
    розгортаються в один рядок — для рідких вкладень це прийнятний компроміс."""
    parts = []
    if el.text:
        parts.append(_esc(el.text))
    for child in el:
        tag = child.tag if isinstance(child.tag, str) else None
        if tag in _DROP_TAGS:
            if child.tail:
                parts.append(_esc(child.tail))
            continue
        if tag == "br":
            parts.append("\n")
        else:
            inner = _render_inline(child)
            if tag == "a":
                href = child.get("href", "")
                if href.startswith(("http://", "https://")) and inner.strip():
                    parts.append(f'<a href="{_esc_attr(href)}">{inner}</a>')
                else:
                    parts.append(inner)
            elif tag in _TG_INLINE and inner.strip():
                t = _TG_INLINE[tag]
                parts.append(f"<{t}>{inner}</{t}>")
            else:
                parts.append(inner)
        if child.tail:
            parts.append(_esc(child.tail))
    return "".join(parts)


def _walk_blocks(el, blocks: list) -> None:
    """Обхід дерева → список самодостатніх блоків Telegram-HTML (один блок = один абзац)."""
    tag = el.tag if isinstance(el.tag, str) else None
    if tag is None or tag in _DROP_TAGS or tag in ("img", "figure", "table"):
        return  # коментарі/шум/нетекстове — у чат-режимі пропускаємо

    if tag in _HEADINGS:
        inner = _render_inline(el).strip()
        if inner:
            blocks.append(f"<b>{inner}</b>")
        return
    if tag == "p":
        inner = _render_inline(el).strip()
        if inner:
            blocks.append(inner)
        return
    if tag == "li":
        inner = _render_inline(el).strip()
        if inner:
            blocks.append(f"• {inner}")
        return
    if tag == "blockquote":
        inner = _render_inline(el).strip()
        if inner:
            blocks.append(f"<blockquote>{inner}</blockquote>")
        return
    if tag == "pre":
        text = el.text_content()
        if text.strip():
            blocks.append(f"<pre>{_esc(text)}</pre>")
        return

    # Контейнер (div/ul/ol/section/article/…): власний текст + рекурсія по дітях.
    if el.text and el.text.strip():
        blocks.append(_esc(el.text.strip()))
    for child in el:
        _walk_blocks(child, blocks)
        if child.tail and child.tail.strip():
            blocks.append(_esc(child.tail.strip()))


def _split_oversized(block: str) -> list:
    """Розрізати блок, довший за ліміт. Знімаємо теги й ріжемо по словах, тоді
    екрануємо кожен шматок наново — так ніколи не розірвемо тег чи HTML-сутність."""
    plain = _html.unescape(re.sub(r"<[^>]+>", "", block))
    out, cur = [], ""
    for word in plain.split():
        while len(word) > _TG_LIMIT:
            out.append(word[:_TG_LIMIT])
            word = word[_TG_LIMIT:]
        if len(cur) + len(word) + 1 > _TG_LIMIT:
            if cur:
                out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        out.append(cur)
    return [_esc(c) for c in out]


def _pack(blocks: list, header: str) -> list:
    """Спакувати блоки в найменшу кількість повідомлень ≤ ліміту."""
    chunks, cur = [], header
    for block in blocks:
        pieces = _split_oversized(block) if len(block) > _TG_LIMIT else [block]
        for piece in pieces:
            sep = "\n\n" if cur else ""
            if len(cur) + len(sep) + len(piece) > _TG_LIMIT:
                if cur:
                    chunks.append(cur)
                cur = piece
            else:
                cur += sep + piece
    if cur:
        chunks.append(cur)
    return chunks


def html_to_chunks(content_html: str, title: str, url: str) -> list:
    """content_html → список повідомлень Telegram-HTML (перше містить заголовок+джерело)."""
    root = _parse(content_html)
    blocks: list = []
    _walk_blocks(root, blocks)
    header = f'<b>{_esc(title)}</b>\n<a href="{_esc_attr(url)}">{_esc(url)}</a>'
    return _pack(blocks, header) or [header]


# --- Режим Instant View: вузли Telegraph --------------------------------

# Telegraph приймає лише цей набір тегів; решту або мапимо, або розгортаємо.
_TGPH_MAP = {
    "h1": "h3", "h2": "h3", "h3": "h3", "h4": "h4", "h5": "h4", "h6": "h4",
    "b": "b", "strong": "strong", "em": "em", "i": "i", "u": "u",
    "s": "s", "strike": "s", "del": "s",
    "a": "a", "code": "code", "pre": "pre", "blockquote": "blockquote",
    "p": "p", "ul": "ul", "ol": "ol", "li": "li", "br": "br", "hr": "hr",
    "figure": "figure", "figcaption": "figcaption", "img": "img", "aside": "aside",
}
_TGPH_VOID = {"br", "hr", "img"}


def _tgph_node(el, base_url: str) -> list:
    """lxml-елемент → список вузлів Telegraph (рядок | {"tag", "attrs", "children"})."""
    tag = el.tag if isinstance(el.tag, str) else None
    if tag is None or tag in _DROP_TAGS:
        return []

    children: list = []
    if el.text:
        children.append(el.text)
    for child in el:
        children.extend(_tgph_node(child, base_url))
        if child.tail:
            children.append(child.tail)

    mapped = _TGPH_MAP.get(tag)
    if mapped is None:
        return children  # невідомий тег (div/span/table/…) — розгортаємо вміст

    node: dict = {"tag": mapped}
    if tag == "a":
        href = el.get("href", "")
        if href:
            node["attrs"] = {"href": urljoin(base_url, href)}
    elif tag == "img":
        src = el.get("src") or el.get("data-src") or ""
        if not src:
            return []
        node["attrs"] = {"src": urljoin(base_url, src)}

    if tag not in _TGPH_VOID and children:
        node["children"] = children
    return [node]


def html_to_telegraph_nodes(content_html: str, base_url: str) -> list:
    """content_html → масив content-вузлів для Telegraph createPage."""
    root = _parse(content_html)
    nodes = _tgph_node(root, base_url)  # корінь <div> розгортається у список дітей
    return [n for n in nodes if not (isinstance(n, str) and not n.strip())]
