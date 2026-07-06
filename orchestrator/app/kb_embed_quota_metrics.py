"""进程内 embedding 配额计数（nf-perf-quotas-embed-chunk）。"""

from __future__ import annotations

from threading import Lock

_lock = Lock()
_acquire_ok = 0
_reject_or_retry = 0
_reject_final = 0
_wait_timeout = 0


def record_embed_acquire_ok() -> None:
    global _acquire_ok
    with _lock:
        _acquire_ok += 1


def record_embed_reject_or_retry() -> None:
    global _reject_or_retry
    with _lock:
        _reject_or_retry += 1


def record_embed_quota_reject_final() -> None:
    global _reject_final
    with _lock:
        _reject_final += 1


def record_embed_quota_wait_timeout() -> None:
    global _wait_timeout
    with _lock:
        _wait_timeout += 1


def snapshot() -> dict[str, int]:
    with _lock:
        return {
            "embed_quota_acquire_ok": _acquire_ok,
            "embed_quota_retry_events": _reject_or_retry,
            "embed_quota_reject_final": _reject_final,
            "embed_quota_wait_timeout": _wait_timeout,
        }
