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
                        if "<form" not in sl and "action=" not in sl:
                            continue
                        for u in _extract_form_action_urls_from_html(s, base):
                            _add(u)
            elif isinstance(obj, list):
                parsed_ok = True
                if fb:
                    for s in _walk_json_strings(obj):
                        if len(s) < 12:
                            continue
                        sl = s.lower()
                        if "<form" not in sl and "action=" not in sl:
                            continue
                        for u in _extract_form_action_urls_from_html(s, fb):
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
