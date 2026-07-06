"""
kb-r2c：Blog/HTML 抓取后的净化（禁止未剥离 HTML 直接 embed）。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser


_SCRIPT_RE = re.compile(r"<script[^>]*>[\s\S]*?</script>", re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>[\s\S]*?</style>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        s = (data or "").strip()
        if s:
            self._chunks.append(s)

    def text(self) -> str:
        return "\n".join(self._chunks)


def strip_html_boilerplate(raw_html: str) -> str:
    """去掉 script/style 与标签；保留可读纯文本（不保证排版完美）。"""
    s = raw_html or ""
    s = _SCRIPT_RE.sub(" ", s)
    s = _STYLE_RE.sub(" ", s)
    parser = _TextExtractor()
    try:
        parser.feed(s)
        parser.close()
        out = parser.text()
    except Exception:
        out = _TAG_RE.sub(" ", s)
    out = _WS_RE.sub(" ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def purify_blog_plain_text(text: str, *, max_chars: int = 24000) -> str:
    """Blog 正文截断 + 与 KB query 一致的红act（可选）。"""
    from app.kb_query_purify import purify_free_text_for_embedding

    return purify_free_text_for_embedding(text or "", max_chars=max_chars)
