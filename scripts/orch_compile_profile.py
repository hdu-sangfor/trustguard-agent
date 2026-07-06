#!/usr/bin/env python3
"""
r4f-c：InstructionCompiler 热路径 profile（脚本化）。

用途：
- 快速对比 ORCH_COMPILER_ENABLE_CACHE 开/关下 compile_plan_item 延时；
- 观察 compile 成功路径在给定 refs/runs 下的均值与 p95。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_ORCH = _ROOT / "orchestrator"
_EXEC = _ROOT / "executor"
for _p in (str(_EXEC), str(_ORCH)):
    while _p in sys.path:
        sys.path.remove(_p)
for _k in list(sys.modules):
    if _k == "app" or _k.startswith("app."):
        del sys.modules[_k]
sys.path.insert(0, str(_ORCH))

from app.core import chunk_store  # type: ignore[import]
from app.core.instruction_compiler import compile_plan_item  # type: ignore[import]
from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem  # type: ignore[import]


def _make_plan(task_id: str, refs: list[ContextChunkRef]) -> PlanItem:
    return PlanItem(
        plan_id="p-profile",
        task_id=task_id,
        skill_id="nmap",
        plan_content="profile compile path",
        context_chunk_refs=refs,
        constraints=PlanConstraints(
            target_scope="host:example.com",
            timeout_seconds=120,
            max_parallelism=1,
        ),
        metadata={},
    )


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    idx = int(round((len(arr) - 1) * 0.95))
    return arr[idx]


def run_profile(*, runs: int, refs_count: int, body_size: int, cache_enabled: bool) -> dict[str, Any]:
    old_cache = os.environ.get("ORCH_COMPILER_ENABLE_CACHE")
    os.environ["ORCH_COMPILER_ENABLE_CACHE"] = "true" if cache_enabled else "false"
    with TemporaryDirectory(prefix="orch-compile-profile-") as td:
        os.environ["WORKSPACE_ROOT"] = td
        task_id = "t-profile"
        refs: list[ContextChunkRef] = []
        for i in range(max(0, refs_count)):
            cid = chunk_store.write_chunk(task_id, chunk_type="ctx", body={"i": i, "blob": "x" * max(0, body_size)})
            refs.append(ContextChunkRef(chunk_id=cid, tenant_id=None))
        item = _make_plan(task_id, refs)
        durs: list[float] = []
        ok_count = 0
        for _ in range(max(1, runs)):
            t0 = time.perf_counter()
            r = compile_plan_item(item, verify_chunks=True)
            durs.append((time.perf_counter() - t0) * 1000.0)
            if r.ok:
                ok_count += 1
        out = {
            "runs": max(1, runs),
            "refs_count": max(0, refs_count),
            "body_size": max(0, body_size),
            "cache_enabled": cache_enabled,
            "ok_count": ok_count,
            "avg_ms": round(statistics.fmean(durs), 3),
            "p95_ms": round(_p95(durs), 3),
            "max_ms": round(max(durs), 3),
        }
    if old_cache is None:
        os.environ.pop("ORCH_COMPILER_ENABLE_CACHE", None)
    else:
        os.environ["ORCH_COMPILER_ENABLE_CACHE"] = old_cache
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Profile compile_plan_item latency (cache on/off).")
    ap.add_argument("--runs", type=int, default=30, help="compile runs for each mode")
    ap.add_argument("--refs", type=int, default=3, help="number of context_chunk_refs")
    ap.add_argument("--body-size", type=int, default=256, help="bytes-ish payload size per chunk body")
    args = ap.parse_args()

    cold = run_profile(runs=args.runs, refs_count=args.refs, body_size=args.body_size, cache_enabled=False)
    warm = run_profile(runs=args.runs, refs_count=args.refs, body_size=args.body_size, cache_enabled=True)
    report = {"profile": "instruction_compiler", "no_cache": cold, "with_cache": warm}
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
