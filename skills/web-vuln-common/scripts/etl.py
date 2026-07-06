"""
ETL: merge discovery outputs → deterministic refined URL list for Nuclei.
Extended (etl-4): matrix param strip, path-template dedup, dispatch scoring.

Shared by dispatcher skill and tests.
"""
from __future__ import annotations

import os
import re
import socket
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

STATIC_EXT_RE = re.compile(
    r"\.(css|js|mjs|map|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|eot|mp4|webm|pdf|zip|tar|gz)$",
    re.I,
)

DEFAULT_DENY_PATH_RE = re.compile(
    r"(logout|signout|log-out|/delete|/remove|/destroy|/reboot|/shutdown|/insert\b|/update\b)(/|$|\?)",
    re.I,
)

_FOLD_NUMERIC_KEYS = frozenset({"id", "page", "offset", "limit", "p", "n"})
_RESERVED_KEYS = frozenset({"action", "cmd", "op", "mode", "method", "type"})

DETERMINISM_VERSION = "etl-4"

# 全串剥离：Session ID 可能含连字符、点号、集群后缀等，避免截断不全
_JSESSIONID_ANYWHERE = re.compile(r";jsessionid=[^/?#]+", re.I)
_MATRIX_IN_PATH = re.compile(r";jsessionid=[^/?#]+", re.I)


def _idna_netloc(netloc: str) -> str:
    if not netloc:
        return netloc
    if "[" in netloc and "]" in netloc:
        return netloc.lower()
    user = ""
    rest = netloc
    if "@" in netloc:
        user, rest = netloc.split("@", 1)
        user = user + "@"
    host = rest
    port = ""
    if rest.startswith("["):
        return netloc.lower()
    if ":" in rest:
        host, maybe_port = rest.rsplit(":", 1)
        if maybe_port.isdigit():
            port = ":" + maybe_port
        else:
            host = rest
    try:
        labels = host.split(".")
        idna_host = ".".join(
            label.encode("idna").decode("ascii") if label else label for label in labels
        )
        return (user + idna_host + port).lower()
    except Exception:
        return netloc.lower()


def apply_host_alias(url: str, host_alias: dict[str, str] | None) -> str:
    if not host_alias:
        return url
    try:
        p = urlparse(url)
    except Exception:
        return url
    host = (p.hostname or "").lower()
    if not host or host not in host_alias:
        return url
    repl = host_alias[host].strip()
    if repl.startswith("http://") or repl.startswith("https://"):
        p2 = urlparse(repl)
        return urlunparse(
            (p2.scheme, p2.netloc, p.path or "/", p.params, p.query, ""),
        )
    repl = repl.lower()
    if ":" in repl and not repl.startswith("["):
        new_netloc = repl
    elif p.port:
        new_netloc = f"{repl}:{p.port}"
    else:
        new_netloc = repl
    scheme = p.scheme or "http"
    return urlunparse((scheme, new_netloc, p.path or "/", p.params, p.query, ""))


def canonicalize_url(url: str, host_alias: dict[str, str] | None = None) -> str:
    u = (url or "").strip()
    if "\\/" in u:
        u = u.replace("\\/", "/")
    if not u:
        return u
    try:
        p = urlparse(u)
    except Exception:
        return u
    u0 = urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))
    u1 = apply_host_alias(u0, host_alias)
    try:
        p = urlparse(u1)
    except Exception:
        return u1
    scheme = (p.scheme or "http").lower()
    netloc = _idna_netloc((p.netloc or "").lower())
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", p.query, ""))


def strip_matrix_params_from_url(url: str) -> str:
    """Remove ;jsessionid=... style matrix parameters from path."""
    u = _JSESSIONID_ANYWHERE.sub("", (url or "").strip())
    try:
        p = urlparse(u)
    except Exception:
        return u
    path = _MATRIX_IN_PATH.sub("", p.path or "/")
    return urlunparse((p.scheme, p.netloc, path, "", p.query, ""))


def host_resolvable(hostname: str) -> bool:
    if not hostname:
        return False
    try:
        socket.getaddrinfo(hostname, None)
        return True
    except Exception:
        return False


def normalize_scope_hostname(hostname: str) -> str:
    """与执行器/指纹脚本一致：本地回环统一映射为容器侧宿主机别名，便于与 allowed_target 对齐。"""
    h = (hostname or "").strip().lower()
    if h in ("localhost", "127.0.0.1", "::1"):
        alias = (
            os.getenv("SCAN_SCOPE_LOCALHOST_ALIAS")
            or os.getenv("EXECUTOR_LOCALHOST_ALIAS")
            or "host.docker.internal"
        ).strip().lower()
        return alias or "host.docker.internal"
    return h


def scope_hosts_for_payload(target_url: str, payload: dict[str, Any] | None) -> set[str]:
    """从任务 target / allowed_target 推导允许的主机集合（精确匹配，不含子域扩张）。"""
    pl = payload or {}
    hosts: set[str] = set()
    for src in (
        target_url,
        str(pl.get("target") or "").strip(),
        str(pl.get("allowed_target") or "").strip(),
    ):
        if not src:
            continue
        s = src if src.startswith(("http://", "https://")) else f"http://{src}"
        try:
            h = normalize_scope_hostname(urlparse(s).hostname or "")
            if h:
                hosts.add(h)
        except Exception:
            continue
    return hosts


def in_scope(url: str, allowed_hosts: set[str] | None, *, allow_subdomains: bool = False) -> bool:
    if not allowed_hosts:
        return True
    try:
        host_only = normalize_scope_hostname(urlparse(url).hostname or "")
    except Exception:
        return False
    if not host_only:
        return False
    if host_only in allowed_hosts:
        return True
    if not allow_subdomains:
        return False
    return any(host_only.endswith("." + ah) for ah in allowed_hosts if ah)


def is_static_url(url: str) -> bool:
    try:
        path = urlparse(url).path or ""
    except Exception:
        return True
    low = path.lower()
    if "/api/" in low or "/rest/" in low or low.endswith(".json") and "/api" in low:
        return False
    return bool(STATIC_EXT_RE.search(path))


def compile_deny_patterns(extra: list[str] | None) -> list[re.Pattern[str]]:
    out: list[re.Pattern[str]] = [DEFAULT_DENY_PATH_RE]
    for raw in extra or []:
        s = (raw or "").strip()
        if not s:
            continue
        try:
            out.append(re.compile(s, re.I))
        except re.error:
            continue
    return out


def is_denied_path(url: str, patterns: list[re.Pattern[str]]) -> bool:
    try:
        p = (urlparse(url).path or "") + "?" + (urlparse(url).query or "")
    except Exception:
        return False
    return any(rx.search(p) for rx in patterns)


def normalize_for_dedup(url: str) -> str:
    try:
        p = urlparse(url)
    except Exception:
        return url.strip()
    scheme = (p.scheme or "http").lower()
    netloc = _idna_netloc((p.netloc or "").lower())
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    q: list[tuple[str, str]] = []
    for k, v in sorted(parse_qsl(p.query, keep_blank_values=True)):
        kl = k.lower()
        if kl in _RESERVED_KEYS:
            q.append((k, v))
            continue
        if kl in _FOLD_NUMERIC_KEYS and v.isdigit():
            v = "{n}"
        q.append((k, v))
    query = urlencode(q)
    return urlunparse((scheme, netloc, path, "", query, ""))


def merge_and_refine(
    katana_urls: list[str],
    dirsearch_urls: list[str],
    *,
    max_urls: int,
    scope_hosts: set[str] | None,
    seed_urls: list[str] | None = None,
    host_alias_map: dict[str, str] | None = None,
    extra_deny_patterns: list[str] | None = None,
    max_input_urls: int = 100_000,
    drop_unresolved_hosts: bool = False,
    allow_scope_subdomains: bool = False,
) -> tuple[list[str], dict[str, int]]:
    deny_rx = compile_deny_patterns(extra_deny_patterns)

    raw: list[str] = []
    for u in (seed_urls or []) + katana_urls + dirsearch_urls:
        if len(raw) >= max_input_urls:
            break
        u = (u or "").strip()
        if not u or not u.startswith(("http://", "https://")):
            continue
        cu = canonicalize_url(u, host_alias_map)
        cu = strip_matrix_params_from_url(cu)
        raw.append(cu)

    stats: dict[str, int] = {
        "raw_in": len(raw),
        "dropped_static": 0,
        "dropped_scope": 0,
        "dropped_deny": 0,
        "deduped": 0,
        "template_deduped": 0,
        "capped": 0,
        "dropped_unresolved": 0,
    }

    unresolved_hosts: set[str] = set()
    seen: set[str] = set()
    refined: list[str] = []

    for u in raw:
        if is_static_url(u):
            stats["dropped_static"] += 1
            continue
        if not in_scope(u, scope_hosts, allow_subdomains=allow_scope_subdomains):
            stats["dropped_scope"] += 1
            continue
        try:
            hn = (urlparse(u).hostname or "").lower()
        except Exception:
            hn = ""
        if hn and not host_resolvable(hn):
            stats["dropped_unresolved"] += 1
            unresolved_hosts.add(hn)
            if drop_unresolved_hosts:
                continue
        if is_denied_path(u, deny_rx):
            stats["dropped_deny"] += 1
            continue
        key = normalize_for_dedup(u)
        if key in seen:
            stats["deduped"] += 1
            continue
        seen.add(key)
        refined.append(key)

    refined.sort()
    if len(refined) > max_urls:
        stats["capped"] = len(refined) - max_urls
        refined = refined[:max_urls]
    stats["unresolved_hosts_count"] = len(unresolved_hosts)
    stats["unresolved_hosts"] = sorted(unresolved_hosts)[:32]
    return refined, stats


def path_template_key(url: str, host_alias: dict[str, str] | None = None) -> str:
    """
    Group URLs like /user/1 and /user/2 under one template key (O(path) per URL).
    """
    u = strip_matrix_params_from_url(canonicalize_url(url, host_alias))
    u = normalize_for_dedup(u)
    try:
        p = urlparse(u)
    except Exception:
        return normalize_for_dedup(url)
    path = p.path or "/"
    segs = [s for s in path.split("/") if s]
    out_segs: list[str] = []
    for seg in segs:
        base = seg.split(";")[0]
        if base.isdigit():
            out_segs.append("{n}")
        else:
            out_segs.append(base.lower())
    tpath = "/" + "/".join(out_segs)
    if len(tpath) > 1 and tpath.endswith("/"):
        tpath = tpath.rstrip("/")
    return urlunparse((p.scheme, p.netloc.lower(), tpath, "", p.query, ""))


def score_endpoint(url: str) -> int:
    low = url.lower()
    s = 0
    if any(x in low for x in (".action", ".do", ".php")):
        s += 8
    if "/api/" in low or "/rest/" in low:
        s += 8
    if any(k in low for k in ("upload", "admin", "config")):
        s += 6
    if "login" in low:
        s += 3
    s += min(len(url), 200) // 40
    return s


def template_dedupe_representatives(urls: list[str], host_alias: dict[str, str] | None = None) -> tuple[list[str], int]:
    """
    Per path-template key, keep the single highest-scoring URL.
    Returns (ordered list by score desc, template_dedup_count).
    """
    best: dict[str, tuple[int, str]] = {}
    for u in urls:
        tk = path_template_key(u, host_alias)
        sc = score_endpoint(u)
        prev = best.get(tk)
        if prev is None or sc > prev[0]:
            best[tk] = (sc, u)
    deduped = len(urls) - len(best)
    ordered = sorted(best.values(), key=lambda x: (-x[0], x[1]))
    return [u for _, u in ordered], deduped


def chunk_urls(urls: list[str], chunk_size: int) -> list[list[str]]:
    if chunk_size <= 0:
        chunk_size = 20
    return [urls[i : i + chunk_size] for i in range(0, len(urls), chunk_size)]


def pick_high_value_endpoints(urls: list[str], limit: int = 10) -> list[str]:
    scored: list[tuple[int, str]] = []
    for u in urls:
        scored.append((score_endpoint(u), u))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [u for _, u in scored[:limit]]


def suspicious_signals(urls: list[str], limit: int = 15) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for u in urls:
        low = u.lower()
        if "upload" in low or "file" in low and "upload" in low:
            out.append({"url": u, "reason": "path_keyword", "confidence": 0.4})
        elif ".action" in low and "upload" in low:
            out.append({"url": u, "reason": "struts_action", "confidence": 0.35})
        if len(out) >= limit:
            break
    return out
