#!/usr/bin/env python3
"""
V1 最终形态就绪检查（最短闭环脚本）。

检查项：
1) Orchestrator /health 的 v1_mq_lanes 契约。
2) Executor /health 的 v1_execution_plane 契约。
3) 双端重叠键取值一致（O-05 车道同源）。
4) Skills run.py 迁移覆盖率（默认目标 100%）。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REQUIRED_MQ_LANE_KEYS = frozenset(
    {
        "execution_dispatch_mode",
        "mq_topic_execute",
        "mq_topic_agent",
        "mq_broker_configured",
        "mq_dispatch_ready",
        "agent_lane_mq_enabled",
        "agent_lane_allowlist_count",
        "agent_lane_routing_active",
        "agent_lane_publish_ready",
    }
)

REQUIRED_EXECUTION_PLANE_KEYS = frozenset(
    {
        "schema_version",
        "execution_dispatch_mode",
        "mq_topic_execute",
        "mq_topic_agent",
        "mq_broker_configured",
        "mq_dispatch_ready",
        "agent_lane_mq_enabled",
        "agent_lane_allowlist_count",
        "agent_lane_routing_active",
        "agent_lane_publish_ready",
        "agent_stdout_summary_default_max_chars",
    }
)

SHARED_LANE_KEYS = frozenset(
    {
        "execution_dispatch_mode",
        "mq_topic_execute",
        "mq_topic_agent",
        "mq_broker_configured",
        "mq_dispatch_ready",
        "agent_lane_mq_enabled",
        "agent_lane_allowlist_count",
        "agent_lane_routing_active",
        "agent_lane_publish_ready",
    }
)


def _http_get_json(url: str, timeout_s: float) -> dict[str, Any]:
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status != 200:
                raise RuntimeError(f"GET {url} status={resp.status}")
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GET {url} invalid json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"GET {url} body is not object")
    return parsed


def summarize_skill_runpy_migration(skills_root: Path) -> dict[str, Any]:
    execute_scripts = sorted(skills_root.glob("*/scripts/execute.py"))
    run_py_files = sorted(skills_root.glob("*/run.py"))
    run_folders = {p.parent.name for p in run_py_files}

    pending_skill_ids: list[str] = []
    for execute in execute_scripts:
        skill_id = execute.parent.parent.name
        if skill_id not in run_folders:
            pending_skill_ids.append(skill_id)
    pending_skill_ids = sorted(set(pending_skill_ids))

    baseline_skill_count = len({p.parent.parent.name for p in execute_scripts})
    migrated_skill_count = baseline_skill_count - len(pending_skill_ids)
    coverage = 1.0
    if baseline_skill_count > 0:
        coverage = migrated_skill_count / baseline_skill_count

    return {
        "baseline_skill_count": baseline_skill_count,
        "migrated_skill_count": migrated_skill_count,
        "pending_skill_ids": pending_skill_ids,
        "runpy_coverage": coverage,
    }


def collect_v1_final_form_errors(
    orchestrator_health: dict[str, Any],
    executor_health: dict[str, Any],
    migration_summary: dict[str, Any],
    min_runpy_coverage: float,
) -> list[str]:
    errors: list[str] = []
    if orchestrator_health.get("status") != "ok":
        errors.append("orchestrator /health status != ok")
    if executor_health.get("status") != "ok":
        errors.append("executor /health status != ok")

    lanes = orchestrator_health.get("v1_mq_lanes")
    if not isinstance(lanes, dict):
        errors.append("orchestrator /health missing v1_mq_lanes")
        lanes = {}
    plane = executor_health.get("v1_execution_plane")
    if not isinstance(plane, dict):
        errors.append("executor /health missing v1_execution_plane")
        plane = {}

    missing_lane_keys = sorted(REQUIRED_MQ_LANE_KEYS - set(lanes.keys()))
    if missing_lane_keys:
        errors.append(f"v1_mq_lanes missing keys: {missing_lane_keys}")
    missing_plane_keys = sorted(REQUIRED_EXECUTION_PLANE_KEYS - set(plane.keys()))
    if missing_plane_keys:
        errors.append(f"v1_execution_plane missing keys: {missing_plane_keys}")

    for key in sorted(SHARED_LANE_KEYS):
        if lanes.get(key) != plane.get(key):
            errors.append(
                f"lane parity mismatch {key}: orchestrator={lanes.get(key)!r} executor={plane.get(key)!r}"
            )

    try:
        coverage = float(migration_summary.get("runpy_coverage", 0.0))
    except (TypeError, ValueError):
        coverage = 0.0
    if coverage < min_runpy_coverage:
        pending = migration_summary.get("pending_skill_ids") or []
        preview = pending[:8]
        errors.append(
            "run.py migration not ready: "
            f"coverage={coverage:.2%} < min={min_runpy_coverage:.2%}; "
            f"pending={preview}"
        )
    return errors


def main() -> int:
    orchestrator_base = (os.getenv("ORCHESTRATOR_URL") or "http://localhost:18081").rstrip("/")
    executor_base = (os.getenv("EXECUTOR_BASE_URL") or "http://localhost:18102").rstrip("/")
    timeout_s = float(os.getenv("V1_FINAL_FORM_TIMEOUT", "12"))
    min_runpy_coverage = float(os.getenv("V1_RUNPY_MIN_COVERAGE", "1.0"))

    repo_root = Path(__file__).resolve().parent.parent
    skills_root = repo_root / "skills"
    migration_summary = summarize_skill_runpy_migration(skills_root)

    try:
        orchestrator_health = _http_get_json(f"{orchestrator_base}/health", timeout_s=timeout_s)
        executor_health = _http_get_json(f"{executor_base}/health", timeout_s=timeout_s)
    except RuntimeError as exc:
        print(f"v1_final_form_readiness: FAILED\n  - {exc}", file=sys.stderr)
        return 1

    errors = collect_v1_final_form_errors(
        orchestrator_health=orchestrator_health,
        executor_health=executor_health,
        migration_summary=migration_summary,
        min_runpy_coverage=min_runpy_coverage,
    )
    if errors:
        print("v1_final_form_readiness: FAILED", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "v1_final_form_readiness: OK "
        f"orchestrator={orchestrator_base} executor={executor_base} "
        f"runpy_coverage={migration_summary['runpy_coverage']:.2%}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
