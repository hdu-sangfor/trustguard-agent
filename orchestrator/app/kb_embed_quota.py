"""
nf-perf-quotas-embed-chunk：KB 向量嵌入调用配额（进程内）。

- 全局：滑动 1 秒窗口内最大调用次数（KB_EMBED_MAX_CALLS_PER_SECOND，0=不限制）。
- 按 scope（通常 workspace_id）：滑动 60 秒窗口内最大次数（KB_EMBED_MAX_CALLS_PER_MINUTE_PER_SCOPE，0=不限制）。
- 超限：KB_EMBED_QUOTA_ON_EXCEED=wait（默认，带超时）或 reject（立即失败）。

与 QdrantKBClient._embed_query 配合；collection 探测 embed 可 skip_quota。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from typing import DefaultDict, List, Optional

_lock = asyncio.Lock()
_global_window: List[float] = []
_scope_windows: DefaultDict[str, List[float]] = defaultdict(list)


class EmbedQuotaExceeded(Exception):
    """嵌入配额拒绝或等待超时。"""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def max_calls_per_second_global() -> int:
    return max(0, _int_env("KB_EMBED_MAX_CALLS_PER_SECOND", 0))


def max_calls_per_minute_per_scope() -> int:
    return max(0, _int_env("KB_EMBED_MAX_CALLS_PER_MINUTE_PER_SCOPE", 0))


def quota_on_exceed_mode() -> str:
    m = (os.getenv("KB_EMBED_QUOTA_ON_EXCEED") or "wait").strip().lower()
    return m if m in ("wait", "reject") else "wait"


def quota_wait_timeout_seconds() -> float:
    ms = max(100, _int_env("KB_EMBED_QUOTA_WAIT_MS_MAX", 8000))
    return ms / 1000.0


def _trim(ts: List[float], cutoff: float) -> None:
    while ts and ts[0] < cutoff:
        ts.pop(0)


async def acquire_kb_embed_slot(quota_scope: Optional[str]) -> None:
    cap_s = max_calls_per_second_global()
    cap_m = max_calls_per_minute_per_scope()
    if cap_s <= 0 and cap_m <= 0:
        return

    mode = quota_on_exceed_mode()
    scope_key = (quota_scope or "").strip() or "_default"
    deadline = time.monotonic() + quota_wait_timeout_seconds()
    from app.kb_embed_quota_metrics import (
        record_embed_acquire_ok,
        record_embed_quota_reject_final,
        record_embed_quota_wait_timeout,
        record_embed_reject_or_retry,
    )

    while True:
        async with _lock:
            now = time.monotonic()
            ok = True
            if cap_s > 0:
                _trim(_global_window, now - 1.0)
                if len(_global_window) >= cap_s:
                    ok = False
            if ok and cap_m > 0:
                _trim(_scope_windows[scope_key], now - 60.0)
                if len(_scope_windows[scope_key]) >= cap_m:
                    ok = False
            if ok:
                if cap_s > 0:
                    _global_window.append(now)
                if cap_m > 0:
                    _scope_windows[scope_key].append(now)
                record_embed_acquire_ok()
                return

            record_embed_reject_or_retry()

        if mode == "reject":
            record_embed_quota_reject_final()
            raise EmbedQuotaExceeded("embed_quota_reject")
        if time.monotonic() >= deadline:
            record_embed_quota_wait_timeout()
            raise EmbedQuotaExceeded("embed_quota_wait_timeout")
        await asyncio.sleep(0.02)
