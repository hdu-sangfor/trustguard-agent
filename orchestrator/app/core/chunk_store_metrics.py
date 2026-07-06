"""Chunk 存储轻量计数器（进程内，供排障与验收；非 Prometheus）。"""
from __future__ import annotations

from threading import Lock

_lock = Lock()
_writes_ok = 0
_writes_fail = 0
_reads_ok = 0
_reads_miss = 0
_reads_denied_tenant = 0
_batch_get_requests = 0
_gc_sweep_runs = 0
_gc_physical_deletes = 0
_lazy_ttl_deletes = 0
_chunk_quota_warn_events = 0


def record_write_ok() -> None:
    global _writes_ok
    with _lock:
        _writes_ok += 1


def record_write_fail() -> None:
    global _writes_fail
    with _lock:
        _writes_fail += 1


def record_read_ok() -> None:
    global _reads_ok
    with _lock:
        _reads_ok += 1


def record_read_miss() -> None:
    global _reads_miss
    with _lock:
        _reads_miss += 1


def record_read_denied_tenant() -> None:
    global _reads_denied_tenant
    with _lock:
        _reads_denied_tenant += 1


def record_batch_get() -> None:
    global _batch_get_requests
    with _lock:
        _batch_get_requests += 1


def record_gc_sweep_run(deleted_count: int) -> None:
    global _gc_sweep_runs, _gc_physical_deletes
    with _lock:
        _gc_sweep_runs += 1
        _gc_physical_deletes += max(0, deleted_count)


def record_lazy_ttl_delete() -> None:
    global _lazy_ttl_deletes
    with _lock:
        _lazy_ttl_deletes += 1


def record_chunk_quota_warn() -> None:
    global _chunk_quota_warn_events
    with _lock:
        _chunk_quota_warn_events += 1


def snapshot() -> dict[str, int]:
    with _lock:
        return {
            "writes_ok": _writes_ok,
            "writes_fail": _writes_fail,
            "reads_ok": _reads_ok,
            "reads_miss": _reads_miss,
            "reads_denied_tenant": _reads_denied_tenant,
            "batch_get_requests": _batch_get_requests,
            "gc_sweep_runs": _gc_sweep_runs,
            "gc_physical_deletes": _gc_physical_deletes,
            "lazy_ttl_deletes": _lazy_ttl_deletes,
            "chunk_quota_warn_events": _chunk_quota_warn_events,
        }
