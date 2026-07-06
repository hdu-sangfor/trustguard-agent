"""chunk 接近每 task 上限时的软告警指标（nf-perf-quotas-embed-chunk）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ROOT = str(REPO_ROOT / "orchestrator")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_chunk_soft_warn_metric_once_per_task(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("CHUNK_MAX_CHUNKS_PER_TASK", "10")
    monkeypatch.setenv("ORCH_CHUNK_QUOTA_WARN_RATIO", "0.5")

    from app.core import chunk_store
    from app.core import chunk_store_metrics

    import importlib

    importlib.reload(chunk_store_metrics)
    importlib.reload(chunk_store)

    snap0 = chunk_store_metrics.snapshot()["chunk_quota_warn_events"]
    tid = "warn-task-1"
    # 阈值=5：第 6 次写入前 current_count==5 触发一次软告警
    for i in range(6):
        chunk_store.write_chunk(tid, chunk_type="t", body={"i": i})
    snap1 = chunk_store_metrics.snapshot()["chunk_quota_warn_events"]
    assert snap1 == snap0 + 1
    chunk_store.write_chunk(tid, chunk_type="t", body={"more": 1})
    snap2 = chunk_store_metrics.snapshot()["chunk_quota_warn_events"]
    assert snap2 == snap1
