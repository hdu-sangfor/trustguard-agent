"""
从共享 discovery 下的 dirsearch.json 提取可作为第二轮 Katana 种子的 URL。

与 skills/web-vuln-common/katana_io.dirsearch_asset_seeds_from_json 语义对齐，
但编排器不依赖 skills 包；状态码含常见重定向，避免「全是 404 时无种子」以外的
「有 301/302 可跟链」场景被漏掉。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_DEFAULT_ALLOWED_STATUS = frozenset({200, 201, 204, 301, 302, 303, 307, 308, 401, 403, 405})
_MATRIX_JSID = re.compile(r";jsessionid=[^/?#]+", re.I)


def _strip_matrix(u: str) -> str:
    return _MATRIX_JSID.sub("", u or "")


def katana_seeds_from_dirsearch_json(
    path: Path,
    *,
    max_seeds: int = 24,
    allowed_status: frozenset[int] | None = None,
) -> list[str]:
    allowed = allowed_status if allowed_status is not None else _DEFAULT_ALLOWED_STATUS
    if not path.is_file() or max_seeds <= 0:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []

    html_like = {"jsp", "jspx", "html", "htm", "xhtml", "php"}
    static_exts = {
        "css",
        "js",
        "mjs",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "svg",
        "ico",
        "webp",
        "woff",
        "woff2",
        "ttf",
        "map",
        "pdf",
        "zip",
    }
    destructive = (
        "logout",
        "signout",
        "log-out",
        "/delete",
        "/remove",
        "/destroy",
        "/reboot",
        "/shutdown",
        "/insert",
        "/update",
    )

    def _status_ok(v: Any) -> bool:
        if v is None:
            return False
        try:
            return int(v) in allowed
        except Exception:
            return False

    def _path_ext(pth: str) -> str:
        pth = (pth or "").rstrip("/")
        last = pth.split("/")[-1] if pth else ""
        if "." not in last:
            return ""
        return last.rsplit(".", 1)[-1].lower()

    def _looks_like_asset(u: str) -> bool:
        if not u.startswith(("http://", "https://")):
            return False
        low = u.lower()
        if any(m in low for m in destructive):
            return False
        try:
            parsed_path = urlparse(u).path or ""
        except Exception:
            parsed_path = ""
        last_seg = parsed_path.rstrip("/").split("/")[-1].lower() if parsed_path else ""
        if last_seg.endswith(".action") or last_seg.endswith(".do"):
            return False
        if parsed_path.endswith("/"):
            return True
        ext = _path_ext(parsed_path)
        if not ext:
            return True
        if ext in static_exts:
            return False
        return ext in html_like

    out: list[str] = []
    seen: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        if not _status_ok(item.get("status")):
            continue
        u = item.get("url")
        if not isinstance(u, str) or not u.startswith(("http://", "https://")):
            continue
        u = _strip_matrix(u.strip())
        if not _looks_like_asset(u):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= max_seeds:
            break
    return out
