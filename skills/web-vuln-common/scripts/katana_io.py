"""Parse Katana output file and Dirsearch JSON — shared by katana skill and tests."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

try:
    from etl import strip_matrix_params_from_url
except ImportError:
    def strip_matrix_params_from_url(url: str) -> str:
        return url or ""

_KATANA_URL_IN_JSON_RE = re.compile(r"https?://[^\s\"'<>]+")
_FORM_ACTION_ATTR_RE = re.compile(
    r'(?is)<form\b[\s\S]*?\baction\s*=\s*(["\'])([^"\']+)\1',
)
_A_HREF_ATTR_RE = re.compile(
    r'(?is)<a\b[\s\S]*?\bhref\s*=\s*(["\'])([^"\']+)\1',
)
_LINK_HREF_ATTR_RE = re.compile(
    r'(?is)<link\b[\s\S]*?\bhref\s*=\s*(["\'])([^"\']+)\1',
)
_SCRIPT_SRC_ATTR_RE = re.compile(
    r'(?is)<script\b[\s\S]*?\bsrc\s*=\s*(["\'])([^"\']+)\1',
)
_WINDOW_LOCATION_RE = re.compile(
    r'(?is)\bwindow\.location(?:\.(?:href|hash|search|pathname))?\s*=\s*(["\'])([^"\']+)\1'
)
_LOCATION_ASSIGN_RE = re.compile(
    r'(?is)\blocation(?:\.(?:href|hash|search|pathname))?\s*=\s*(["\'])([^"\']+)\1'
)
_WINDOW_LOCATION_ASSIGN_RE = re.compile(
    r'(?is)\bwindow\.location(?:\.href)?\s*=\s*(["\'])([^"\']+)\1'
)
_WINDOW_LOCATION_REPLACE_RE = re.compile(
    r'(?is)\bwindow\.location\s*\.?\s*replace\s*\(\s*(["\'])([^"\']+)\1\s*\)'
)
_LOCATION_REPLACE_RE = re.compile(
    r'(?is)\blocation\s*\.?\s*replace\s*\(\s*(["\'])([^"\']+)\1\s*\)'
)


def _sanitize_katana_url_candidate(u: str) -> str:
    u = (u or "").strip()
    if "\\/" in u:
        u = u.replace("\\/", "/")
    return u


def _walk_json_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_json_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_json_strings(v)
    elif isinstance(obj, str):
        yield obj


def _base_url_from_katana_obj(obj: dict[str, Any]) -> str:
    req = obj.get("request")
    if isinstance(req, dict):
        u = req.get("url")
        if isinstance(u, str) and u.startswith(("http://", "https://")):
            return _sanitize_katana_url_candidate(u)
    u = obj.get("url")
    if isinstance(u, str) and u.startswith(("http://", "https://")):
        return _sanitize_katana_url_candidate(u)
    return ""


def _extract_form_action_urls_from_html(html: str, base: str) -> list[str]:
    out: list[str] = []
    base = (base or "").strip()
    if not base.startswith(("http://", "https://")):
        return out
    for m in _FORM_ACTION_ATTR_RE.finditer(html):
        val = (m.group(2) or "").strip()
        if not val or val.lower().startswith("javascript:") or val.startswith("#"):
            continue
        if val.startswith(("http://", "https://")):
            out.append(_sanitize_katana_url_candidate(val))
        else:
            out.append(_sanitize_katana_url_candidate(urljoin(base, val)))
    return out


def _extract_href_urls_from_html(html: str, base: str) -> list[str]:
    """
    从 HTML 片段提取“前端导航特征”URL：
    - <a href="...">
    - <link href="...">
    - <script src="...">（只作为链接种子，不强制 Content-Type）
    - window.location / location 赋值/replace（JS 导航）
    """
    out: list[str] = []
    base = (base or "").strip()
    if not base.startswith(("http://", "https://")):
        return out

    def _maybe_add(raw: str) -> None:
        v = (raw or "").strip()
        if not v:
            return
        lv = v.lower()
        if lv.startswith("javascript:"):
            return
        if v.startswith("#"):
            return
        if v.startswith(("http://", "https://")):
            out.append(_sanitize_katana_url_candidate(v))
        else:
            out.append(_sanitize_katana_url_candidate(urljoin(base, v)))

    for m in _A_HREF_ATTR_RE.finditer(html):
        _maybe_add(m.group(2) or "")
    for m in _LINK_HREF_ATTR_RE.finditer(html):
        _maybe_add(m.group(2) or "")
    for m in _SCRIPT_SRC_ATTR_RE.finditer(html):
        _maybe_add(m.group(2) or "")

    # window.location = '...'
    for m in _WINDOW_LOCATION_RE.finditer(html):
        _maybe_add(m.group(2) or "")
    for m in _LOCATION_ASSIGN_RE.finditer(html):
        _maybe_add(m.group(2) or "")
    for m in _WINDOW_LOCATION_ASSIGN_RE.finditer(html):
        _maybe_add(m.group(2) or "")

    # window.location.replace('...')
    for m in _WINDOW_LOCATION_REPLACE_RE.finditer(html):
        _maybe_add(m.group(2) or "")
    for m in _LOCATION_REPLACE_RE.finditer(html):
        _maybe_add(m.group(2) or "")

    return out


def katana_urls_from_file(path: Path, fallback_base: str = "") -> list[str]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    fb = _sanitize_katana_url_candidate(fallback_base.strip())

    def _add(u: str) -> None:
        u = _sanitize_katana_url_candidate(u)
        u = strip_matrix_params_from_url(u)
        if not u.startswith(("http://", "https://")):
            return
        if u in seen:
            return
        seen.add(u)
        out.append(u)

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("http://") or line.startswith("https://"):
            _add(line)
            continue
        if line.startswith("{"):
            parsed_ok = False
            obj: Any = None
            try:
                obj = json.loads(line)
            except Exception:
                obj = None
            if isinstance(obj, dict):
                parsed_ok = True
                req = obj.get("request")
                if isinstance(req, dict) and isinstance(req.get("url"), str):
                    _add(req["url"])
                if isinstance(obj.get("url"), str):
                    _add(obj["url"])
                if isinstance(obj.get("qurl"), str):
                    _add(obj["qurl"])
                for k in ("endpoint", "matched", "source"):
                    v = obj.get(k)
                    if isinstance(v, str) and v.startswith(("http://", "https://")):
                        _add(v)
                base = _base_url_from_katana_obj(obj) or fb
                if base:
                    for s in _walk_json_strings(obj):
                        if len(s) < 12:
                            continue
                        sl = s.lower()
                        # 抽取“前端导航特征”；只要片段里出现这些关键字就进入解析（减少误报/加快速度）
                        if not (
                            ("<form" in sl and "action=" in sl)
                            or ("<a" in sl and "href=" in sl)
                            or ("<link" in sl and "href=" in sl)
                            or ("<script" in sl and "src=" in sl)
                            or ("window.location" in sl)
                            or ("location.href" in sl)
                            or ("location=" in sl)
                        ):
                            continue
                        for u in _extract_form_action_urls_from_html(s, base):
                            _add(u)
                        for u in _extract_href_urls_from_html(s, base):
                            _add(u)
            elif isinstance(obj, list):
                parsed_ok = True
                if fb:
                    for s in _walk_json_strings(obj):
                        if len(s) < 12:
                            continue
                        sl = s.lower()
                        if not (
                            ("<form" in sl and "action=" in sl)
                            or ("<a" in sl and "href=" in sl)
                            or ("<link" in sl and "href=" in sl)
                            or ("<script" in sl and "src=" in sl)
                            or ("window.location" in sl)
                            or ("location.href" in sl)
                            or ("location=" in sl)
                        ):
                            continue
                        for u in _extract_form_action_urls_from_html(s, fb):
                            _add(u)
                        for u in _extract_href_urls_from_html(s, fb):
                            _add(u)
            if not parsed_ok:
                for m in _KATANA_URL_IN_JSON_RE.findall(line):
                    _add(m)
            continue
        for m in _KATANA_URL_IN_JSON_RE.findall(line):
            _add(m)
    return out


def dirsearch_urls_from_json(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    urls: list[str] = []
    if isinstance(data, dict) and "results" in data:
        for item in data.get("results") or []:
            if isinstance(item, dict):
                u = item.get("url")
                if isinstance(u, str) and u.startswith(("http://", "https://")):
                    urls.append(strip_matrix_params_from_url(u))
    return urls


def dirsearch_asset_seeds_from_json(
    path: Path,
    *,
    allowed_status: tuple[int, ...] = (200, 201, 204, 301, 302, 303, 307, 308, 401, 403, 405),
    max_assets: int = 200,
) -> list[str]:
    """
    从 dirsearch.json 中提取“可作为 Katana seed 的资产”URL：
    - 只保留 allowed_status 中的结果（默认 200/403）
    - 以目录/HTML 页面为主：路径以 `/` 结尾、或无明显静态扩展名、或常见 HTML 扩展名（jsp/html/htm/xhtml）
    - 排除明显静态资源与 action/do 端点（避免把后端注入端点当作爬取 seed）

    说明：这里不做真实 Content-Type 探测，只用 URL 形态做“偏向 text/html”的启发式过滤。
    """
    if not path.exists() or max_assets <= 0:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    results = []
    if isinstance(data, dict) and "results" in data:
        results = data.get("results") or []
    if not isinstance(results, list):
        return []

    html_like_exts = {
        "jsp",
        "jspx",
        "html",
        "htm",
        "xhtml",
        "php",  # 部分展示页面可能是 php；ETL 后续仍会做静态过滤
    }
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
        "svgz",
        "eot",
        "pdf",
        "zip",
        "tar",
        "gz",
        "mp4",
        "webm",
    }
    destructive_markers = (
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

    def _status_ok(v: object) -> bool:
        if v is None:
            return False
        try:
            return int(v) in set(allowed_status)
        except Exception:
            return False

    def _path_ext(p: str) -> str:
        p = p or ""
        # strip trailing slash so last segment works
        if p.endswith("/"):
            p = p.rstrip("/")
        last = p.split("/")[-1] if p else ""
        if "." not in last:
            return ""
        return last.rsplit(".", 1)[-1].lower()

    def _looks_like_asset(u: str) -> bool:
        if not u.startswith(("http://", "https://")):
            return False
        low = u.lower()
        if any(m in low for m in destructive_markers):
            return False
        # 只根据 URL path 判断静态扩展名，避免把域名里的静态后缀子串误判
        try:
            parsed_path = urlparse(u).path or ""
        except Exception:
            parsed_path = ""

        if parsed_path.endswith("/"):
            return True
        ext = _path_ext(parsed_path)
        if not ext:
            # no extension => likely directory-ish / route
            return True
        if ext in static_exts:
            return False
        # .action / .do 等动态端点不再在此处一刀切过滤，交由后续 ETL + Nuclei 阶段基于路径模板与启发式评分决定重要性
        return ext in html_like_exts or ext in {"jsp", "jspx", "php", "action", "do"}

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
        u = strip_matrix_params_from_url(u)
        if not _looks_like_asset(u):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= max_assets:
            break
    return out
