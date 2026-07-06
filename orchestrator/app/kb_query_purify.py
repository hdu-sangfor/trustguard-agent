"""
kb-r3a：KB 向量检索 query 净化 — 不把裸 IP/URL/长 history 原样写入 embed 输入。

目标 IP/URL 仍由 Qdrant filter / 任务上下文承载；此处仅影响 query_text 嵌入语义。
"""

from __future__ import annotations

import os
import re
from typing import Mapping
from urllib.parse import urlparse

_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)
_URL_RE = re.compile(r"https?://[^\s\)\]\}'\",]+", re.IGNORECASE)
# 粗略 IPv6（可能误伤十六进制串，仅用于 history/todo 等自由文本）
_IPV6_LIKE_RE = re.compile(
    r"\b(?:[0-9a-fA-F]{1,4}:){2,}[0-9a-fA-F:]{3,}\b"
)


def kb_query_purify_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    v = (env.get("KB_QUERY_PURIFY") or "true").strip().lower()
    return v not in ("0", "false", "no", "off")


def kb_query_history_max_chars(environ: Mapping[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    raw = (env.get("KB_QUERY_HISTORY_MAX_CHARS") or "").strip()
    if not raw:
        return 384
    try:
        return max(0, min(int(raw), 8000))
    except ValueError:
        return 384


def kb_query_todo_max_chars(environ: Mapping[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    raw = (env.get("KB_QUERY_TODO_MAX_CHARS") or "").strip()
    if not raw:
        return 256
    try:
        return max(0, min(int(raw), 4000))
    except ValueError:
        return 256


def redact_ips_and_urls(text: str) -> str:
    s = _URL_RE.sub("[url]", text or "")
    s = _IPV4_RE.sub("[ip]", s)
    s = _IPV6_LIKE_RE.sub("[ip6]", s)
    return s


def embed_target_hint(raw: str) -> str:
    """将任务 target 压缩为不含具体主机/IP 的类别提示。"""
    t = (raw or "").strip()
    if not t:
        return "target_absent=true"
    low = t.lower()
    if "://" in t:
        try:
            p = urlparse(t)
            scheme = (p.scheme or "url").lower()
            return f"target_scheme={scheme}"
        except Exception:
            return "target_scheme=url"
    if _IPV4_RE.fullmatch(t):
        return "target_kind=ipv4"
    if _IPV6_LIKE_RE.search(t) and len(t) < 128:
        return "target_kind=ipv6"
    if "/" not in t and " " not in t and "." in t:
        return "target_kind=hostname"
    return "target_kind=text"


def purify_free_text_for_embedding(
    text: str,
    *,
    max_chars: int,
    tail_biased: bool = False,
) -> str:
    """
    压缩自由文本以便作为 KB 检索查询的一部分。

    tail_biased=False（默认）：保留前 max_chars（传统行为，适合 todo_name/desc 等静态字段）。
    tail_biased=True：保留末尾 max_chars（适合 history_summary 等按时间追加的日志，
        避免最新事件被首部的旧事件挤出查询 —— 已在 task-0385... 日志中观察到
        首轮 httpx 事件永久固化在 query_text 头部、新发现被 `[truncated]` 丢弃的问题）。
    """
    if max_chars <= 0:
        return ""
    s = redact_ips_and_urls(text or "").strip()
    if not s:
        return ""
    if len(s) > max_chars:
        if tail_biased:
            s = "…[truncated]" + s[-max_chars:]
        else:
            s = s[:max_chars].rstrip() + "…[truncated]"
    return s


def format_available_skills_hint(skill_ids: list[str] | None, *, max_items: int = 24, max_chars: int = 400) -> str:
    if not skill_ids or max_chars <= 0:
        return ""
    seen: list[str] = []
    for s in skill_ids:
        x = str(s or "").strip()
        if x and x not in seen:
            seen.append(x)
        if len(seen) >= max_items:
            break
    if not seen:
        return ""
    out = ",".join(seen)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip(",") + "…"
    return out


def build_purified_kb_embed_query_text(
    *,
    phase: str,
    target_raw: str,
    todo_name: str,
    todo_desc: str,
    history_summary: str,
    available_skill_ids: list[str] | None,
    environ: Mapping[str, str] | None = None,
) -> str:
    env = environ if environ is not None else os.environ
    hmax = kb_query_history_max_chars(env)
    tmax = kb_query_todo_max_chars(env)

    parts: list[str] = [f"phase={phase}", embed_target_hint(target_raw)]

    sk = format_available_skills_hint(available_skill_ids, max_items=24, max_chars=400)
    if sk:
        parts.append(f"available_skills={sk}")

    tn = purify_free_text_for_embedding(todo_name, max_chars=min(tmax, 200))
    if tn:
        parts.append(f"todo={tn}")

    td = purify_free_text_for_embedding(todo_desc, max_chars=tmax)
    if td:
        parts.append(f"todo_description={td}")

    # history_summary 是按时间追加的动作账本，必须 tail_biased 以保留最近事件；
    # 否则 KB 查询会被首轮 httpx/katana 行永久占据，新 nuclei/exploit 证据丢失。
    hs = purify_free_text_for_embedding(history_summary, max_chars=hmax, tail_biased=True)
    if hs:
        parts.append(f"history_summary={hs}")

    return "\n".join(parts)
