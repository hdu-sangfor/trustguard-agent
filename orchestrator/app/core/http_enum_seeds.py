"""从 http-enum 产物中抽取可喂给 dispatcher/nuclei 的种子 URL（不依赖 Katana）。

URL 智能降噪：normalize_url_for_pipeline 对查询串做缓存击穿/追踪/Token 剥离与按键排序，
用于去重与降低 Nuclei 重复扫描；ORCHESTRATOR_NORMALIZE_STRICT=0 时仅做会话类清洗。
"""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

_FORM_ACTION_ATTR_RE = re.compile(
    r'(?is)<form\b[\s\S]*?\baction\s*=\s*(["\'])([^"\']+)\1',
)
_JSESSIONID_SEG = re.compile(r";jsessionid=[^/?#]+", re.I)

# 查询串：会话类（与路径 matrix 互补）
_SESSION_QUERY_KEYS = frozenset(
    {
        "phpsessid",
        "jsessionid",
        "asp.net_sessionid",
        "sessionid",
        "sid",
    }
)

# 缓存击穿 / 时间戳 / 随机数（严格模式下剥离；含常见 _t / _ts）
_CACHE_BUSTER_RE = re.compile(
    r"^(_t|_ts|t|ts|timestamp|time|v|ver|version|v_|rnd|random|nonce|cb|cache|_)(\d*)$",
    re.I,
)

# 动态安全 Token（Nuclei 通常无法自动带参时剥离）
_SECURITY_TOKEN_RE = re.compile(
    r"^(csrf|xsrf|token|_token|authenticity_token|state|oauth_token|csrf_token|xsrf-token|__requestverificationtoken)$",
    re.I,
)

# 营销 / 追踪
_TRACKING_RE = re.compile(
    r"^(utm_[a-z0-9_]+|from|source|spm|ref|callback|gclid|fbclid|mc_[a-z0-9_]+)$",
    re.I,
)


def _normalize_strict_enabled() -> bool:
    return (os.getenv("ORCHESTRATOR_NORMALIZE_STRICT", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _should_drop_query_key_for_denoise(key: str) -> bool:
    k = (key or "").strip()
    if not k:
        return False
    kl = k.lower()
    if kl in _SESSION_QUERY_KEYS:
        return True
    if _CACHE_BUSTER_RE.match(kl):
        return True
    if _SECURITY_TOKEN_RE.match(kl):
        return True
    if _TRACKING_RE.match(kl):
        return True
    return False

# 文档类扩展名：最后一个 path 段带这些后缀时视为「文件 URL」，不把 /app 改成 /app/（避免误伤）
_DOC_LIKE_EXT = frozenset(
    {
        "html",
        "htm",
        "xhtml",
        "jsp",
        "jspx",
        "php",
        "php3",
        "asp",
        "aspx",
        "do",
        "action",
        "js",
        "css",
        "json",
        "xml",
        "svg",
    }
)


def _dedupe_preserve(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _last_path_segment(path: str) -> str:
    p = (path or "").strip()
    if not p or p == "/":
        return ""
    return p.rstrip("/").rsplit("/", 1)[-1]


def _seg_looks_like_file_name(seg: str) -> bool:
    if not seg or "." not in seg or seg.startswith("."):
        return False
    ext = seg.rsplit(".", 1)[-1].lower()
    return ext in _DOC_LIKE_EXT


def _normalize_base_url_for_relative_join(base: str) -> str:
    """
    相对 action（如 doUpload.action）需相对「目录」解析。若 base 为 http://host/app 且无末尾斜杠，
    urllib 会按「替换最后一级」处理，得到 http://host/doUpload.action（错误）。目录型 base 补成 /app/。
    """
    b = (base or "").strip()
    try:
        p = urlparse(b)
    except Exception:
        return b
    if not p.scheme or not p.netloc:
        return b
    path = p.path or "/"
    if path == "/" or path.endswith("/"):
        return b
    last = _last_path_segment(path)
    if _seg_looks_like_file_name(last):
        return b
    new_path = path + "/"
    return urlunparse((p.scheme, p.netloc, new_path, p.params, p.query, p.fragment))


def _strip_jsessionid_matrix(u: str) -> str:
    s = _JSESSIONID_SEG.sub("", u or "")
    try:
        p = urlparse(s)
        path = (p.path or "/").split(";")[0]
        return urlunparse((p.scheme, p.netloc, path or "/", "", p.query, ""))
    except Exception:
        return s


def strip_session_noise_from_url(url: str) -> str:
    """
    轻量模式（ORCHESTRATOR_NORMALIZE_STRICT=0 时 normalize_url_for_pipeline 仅用本逻辑）：
    路径 matrix 中的 ;jsessionid、查询串中的 PHPSESSID 等。
    """
    s = _strip_jsessionid_matrix(url or "")
    try:
        p = urlparse(s)
        q = p.query or ""
        if not q:
            return s.strip()
        parts = [x for x in q.split("&") if x and "phpsessid=" not in x.lower()]
        q2 = "&".join(parts)
        if q2 != q:
            s = urlunparse((p.scheme, p.netloc, p.path or "/", p.params, q2, p.fragment))
    except Exception:
        pass
    return s.strip()


def normalize_url_for_pipeline(url: str) -> str:
    """
    URL 智能降噪与规范化：去重/提速/可读（IML、dispatcher、nuclei 种子共用）。

    - 默认（ORCHESTRATOR_NORMALIZE_STRICT=1）：路径 matrix 清理 + 剥离会话/缓存/Token/追踪参数 + 剩余 query 按键名排序。
    - STRICT=0：仅 strip_session_noise_from_url（避免误杀业务参数名如 t）。
    """
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return u
    if not _normalize_strict_enabled():
        return strip_session_noise_from_url(u)

    u = _strip_jsessionid_matrix(u)
    try:
        p = urlparse(u)
        path = (p.path or "/").split(";")[0]
        q = p.query or ""
        if not q:
            return urlunparse((p.scheme, p.netloc, path or "/", p.params, "", p.fragment)).strip()

        pairs = parse_qsl(q, keep_blank_values=True)
        kept: list[tuple[str, str]] = []
        for k, v in pairs:
            if _should_drop_query_key_for_denoise(k):
                continue
            kept.append((k, v))
        kept.sort(key=lambda kv: (kv[0].lower(), kv[1]))
        q2 = urlencode(kept, doseq=True)
        return urlunparse((p.scheme, p.netloc, path or "/", p.params, q2, p.fragment)).strip()
    except Exception:
        return strip_session_noise_from_url(u)


def extract_http_enum_seed_urls(target_context: dict[str, Any] | None) -> list[str]:
    """
    解析 http-enum_raw_preview 中的 <form action="...">，与 http-enum_url 拼接为绝对 URL，
    并剥离 ;jsessionid=... 矩阵参数，避免 Katana/Nuclei 路径拼接误报。
    """
    if not isinstance(target_context, dict) or not target_context:
        return []
    raw = str(target_context.get("http-enum_raw_preview") or "")
    base = str(
        target_context.get("http-enum_url")
        or target_context.get("target")
        or "",
    ).strip()
    if not raw or not base.startswith(("http://", "https://")):
        return []
    out: list[str] = []
    for m in _FORM_ACTION_ATTR_RE.finditer(raw):
        val = (m.group(2) or "").strip()
        if not val or val.lower().startswith("javascript:") or val.startswith("#"):
            continue
        if val.startswith(("http://", "https://")):
            u = val
        else:
            u = urljoin(_normalize_base_url_for_relative_join(base), val)
        u = normalize_url_for_pipeline(u)
        if u.startswith(("http://", "https://")):
            out.append(u)
    return _dedupe_preserve(out)
