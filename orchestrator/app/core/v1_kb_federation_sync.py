"""
KB 联邦旁路存储后台一致性校正（PoC）。

- 独立异步循环，不阻塞 tick，不调用 state_machine。
- 仅结构化日志（计数）；不记录元数据正文。
"""

from __future__ import annotations

import os
from typing import Any

from app.core.v1_kb_federation_store import (
    get_kb_federation_meta_store,
    kb_federation_store_enabled_from_env,
)


def kb_federation_sync_enabled_from_env() -> bool:
    raw = (os.getenv("V1_KB_FEDERATION_SYNC_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def kb_federation_sync_interval_seconds() -> int:
    try:
        return max(0, int(os.getenv("V1_KB_FEDERATION_SYNC_INTERVAL_SECONDS", "0") or "0"))
    except ValueError:
        return 0


def run_kb_federation_reconcile_once() -> dict[str, int] | None:
    """
    单次校正索引；需同时开启存储与同步 Flag。
    供后台循环与单测直接调用。
    """
    if not kb_federation_store_enabled_from_env():
        return None
    if not kb_federation_sync_enabled_from_env():
        return None
    store = get_kb_federation_meta_store()
    return store.reconcile_indexes()
