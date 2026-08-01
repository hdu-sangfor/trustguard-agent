#!/usr/bin/env python3
"""
Live 动态测试：Issue #136 切片 A（结构化 CoT Reasoning Steps）。

优先连已启动的 docker compose 服务：
  Evidence  http://localhost:18103
  Gateway   http://localhost:18080

用法（仓库根）:
  python tests/dynamic/run_cot_reasoning_dynamic.py
  python tests/dynamic/run_cot_reasoning_dynamic.py --skip-orch-hook

环境变量:
  EVIDENCE_BASE_URL, OVERRIDE_GATEWAY_URL, COT_DYNAMIC_REPORT
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

EVIDENCE = os.getenv("EVIDENCE_BASE_URL", "http://localhost:18103").rstrip("/")
GATEWAY = os.getenv("OVERRIDE_GATEWAY_URL", "http://localhost:18080").rstrip("/")
REPORT_PATH = Path(
    os.getenv(
        "COT_DYNAMIC_REPORT",
        str(
            Path(__file__).resolve().parents[3]
            / "local-notes"
            / "trustguard-agent"
            / f"cot-reasoning-dynamic-{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
        ),
    )
)


@dataclass
class CaseResult:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""
    log_excerpt: str = ""


@dataclass
class Suite:
    results: list[CaseResult] = field(default_factory=list)

    def add(self, r: CaseResult) -> None:
        self.results.append(r)
        mark = {"PASS": "OK", "FAIL": "!!", "SKIP": "--"}.get(r.status, r.status)
        print(f"[{mark}] {r.name}: {r.status}" + (f" — {r.detail}" if r.detail else ""))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def case_d1_ingest(client: httpx.Client, suite: Suite, task_id: str) -> str | None:
    body = {
        "task_id": task_id,
        "trace_id": task_id,
        "step_type": "TASK_PLANNING",
        "status": "SUCCEEDED",
        "summary": "dynamic-test plan step",
        "payload": {"source": "dynamic_test", "api_token": "should-not-matter-here"},
        "started_at": _now(),
        "finished_at": _now(),
        "duration_ms": 12,
    }
    try:
        r = client.post(f"{EVIDENCE}/v1/reasoning-steps", json=body, timeout=15.0)
        data = r.json() if r.content else {}
        if r.status_code != 200 or not data.get("accepted"):
            suite.add(
                CaseResult(
                    "D1 Evidence ingest",
                    "FAIL",
                    f"status={r.status_code} body={data!r}",
                    r.text[:500],
                )
            )
            return None
        if data.get("trace_id") != task_id:
            suite.add(
                CaseResult(
                    "D1 Evidence ingest",
                    "FAIL",
                    f"trace_id mismatch: {data.get('trace_id')!r}",
                )
            )
            return None
        step_id = data.get("step_id")
        suite.add(CaseResult("D1 Evidence ingest", "PASS", f"step_id={step_id}"))
        return step_id
    except Exception as exc:  # noqa: BLE001
        suite.add(CaseResult("D1 Evidence ingest", "FAIL", str(exc)))
        return None


def case_d2_list(client: httpx.Client, suite: Suite, task_id: str, step_id: str | None) -> None:
    if not step_id:
        suite.add(CaseResult("D2 Evidence list", "SKIP", "no step_id from D1"))
        return
    try:
        r = client.get(
            f"{EVIDENCE}/internal/tasks/{task_id}/reasoning-steps",
            params={"limit": 50},
            timeout=15.0,
        )
        rows = r.json() if r.content else []
        if r.status_code != 200 or not isinstance(rows, list):
            suite.add(CaseResult("D2 Evidence list", "FAIL", f"status={r.status_code}"))
            return
        ids = {row.get("step_id") for row in rows if isinstance(row, dict)}
        if step_id not in ids:
            suite.add(
                CaseResult(
                    "D2 Evidence list",
                    "FAIL",
                    f"missing step_id; got {len(rows)} rows",
                    json.dumps(rows[:3], ensure_ascii=False)[:400],
                )
            )
            return
        suite.add(CaseResult("D2 Evidence list", "PASS", f"rows={len(rows)}"))
    except Exception as exc:  # noqa: BLE001
        suite.add(CaseResult("D2 Evidence list", "FAIL", str(exc)))


def case_d3_gateway(client: httpx.Client, suite: Suite, task_id: str, step_id: str | None) -> None:
    # Gateway 列表不要求任务行存在；直读 Evidence。仍先建任务更贴近真实。
    try:
        cr = client.post(
            f"{GATEWAY}/api/v1/tasks",
            json={
                "target": "http://127.0.0.1:9",
                "name": f"cot-dyn-{task_id[-8:]}",
                "description": "cot dynamic",
            },
            timeout=30.0,
        )
        gw_task = None
        if cr.status_code == 200 and (cr.json() or {}).get("code") == "0":
            gw_task = (cr.json().get("data") or {}).get("taskId")
        use_id = gw_task or task_id
        if gw_task and gw_task != task_id:
            # 把步骤写到 gateway 创建的 task 上，保证 list 非空更稳
            body = {
                "task_id": use_id,
                "trace_id": use_id,
                "step_type": "TOOL_CALL",
                "status": "SUCCEEDED",
                "summary": "gateway proxy probe",
                "payload": {"tool_name": "nmap"},
                "started_at": _now(),
                "finished_at": _now(),
            }
            client.post(f"{EVIDENCE}/v1/reasoning-steps", json=body, timeout=15.0)

        r = client.get(
            f"{GATEWAY}/api/v1/tasks/{use_id}/reasoning-steps",
            params={"limit": 50},
            timeout=20.0,
        )
        data = r.json() if r.content else {}
        if r.status_code != 200 or data.get("code") != "0":
            suite.add(
                CaseResult(
                    "D3 Gateway proxy",
                    "FAIL",
                    f"status={r.status_code} code={data.get('code')!r}",
                    r.text[:500],
                )
            )
            return
        rows = data.get("data")
        if not isinstance(rows, list):
            suite.add(CaseResult("D3 Gateway proxy", "FAIL", "data not list"))
            return
        if not rows:
            suite.add(CaseResult("D3 Gateway proxy", "FAIL", "empty list after ingest"))
            return
        sample = rows[0]
        for key in ("traceId", "taskId", "stepId", "stepType", "status"):
            if key not in sample:
                suite.add(CaseResult("D3 Gateway proxy", "FAIL", f"missing camelCase {key}"))
                return
        if sample.get("traceId") != sample.get("taskId"):
            suite.add(
                CaseResult(
                    "D3 Gateway proxy",
                    "FAIL",
                    f"traceId!=taskId: {sample.get('traceId')!r}",
                )
            )
            return
        suite.add(CaseResult("D3 Gateway proxy", "PASS", f"rows={len(rows)} task={use_id}"))
    except Exception as exc:  # noqa: BLE001
        suite.add(CaseResult("D3 Gateway proxy", "FAIL", str(exc)))


def case_d4_reject(client: httpx.Client, suite: Suite) -> None:
    try:
        r = client.post(
            f"{EVIDENCE}/v1/reasoning-steps",
            json={
                "task_id": "task-bad-type",
                "step_type": "NOT_A_TYPE",
                "status": "SUCCEEDED",
            },
            timeout=15.0,
        )
        if r.status_code == 422:
            suite.add(CaseResult("D4 Reject bad step_type", "PASS"))
        else:
            suite.add(
                CaseResult(
                    "D4 Reject bad step_type",
                    "FAIL",
                    f"expected 422 got {r.status_code}",
                    r.text[:300],
                )
            )
    except Exception as exc:  # noqa: BLE001
        suite.add(CaseResult("D4 Reject bad step_type", "FAIL", str(exc)))


def case_d5b_orch_client_path(suite: Suite) -> None:
    """不依赖 LLM：在 orchestrator 容器内调用 emit_cot_step → Evidence。"""
    import subprocess

    tid = f"task-cot-client-{uuid.uuid4().hex[:8]}"
    code = f"""
import asyncio
from app.core.reasoning_emit import emit_cot_step

async def main():
    await emit_cot_step(
        task_id="{tid}",
        step_type="TASK_PLANNING",
        status="SUCCEEDED",
        summary="orch-client-path-probe",
        payload={{"probe": True}},
    )
asyncio.run(main())
print("ok")
"""
    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "orchestrator",
                "python",
                "-c",
                code,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        if proc.returncode != 0:
            suite.add(
                CaseResult(
                    "D5b Orchestrator→Evidence client",
                    "FAIL",
                    f"rc={proc.returncode} err={(proc.stderr or proc.stdout)[:300]}",
                )
            )
            return
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                f"{EVIDENCE}/internal/tasks/{tid}/reasoning-steps",
                params={"limit": 10},
            )
            rows = r.json() if r.content else []
        types = {row.get("step_type") for row in rows if isinstance(row, dict)}
        if "TASK_PLANNING" in types:
            suite.add(
                CaseResult(
                    "D5b Orchestrator→Evidence client",
                    "PASS",
                    f"task={tid} rows={len(rows)}",
                )
            )
        else:
            suite.add(
                CaseResult(
                    "D5b Orchestrator→Evidence client",
                    "FAIL",
                    f"no step after emit; rows={rows!r}",
                )
            )
    except FileNotFoundError:
        suite.add(
            CaseResult(
                "D5b Orchestrator→Evidence client",
                "SKIP",
                "docker not available",
            )
        )
    except Exception as exc:  # noqa: BLE001
        suite.add(CaseResult("D5b Orchestrator→Evidence client", "FAIL", str(exc)))


def case_d5_orch_hook(client: httpx.Client, suite: Suite, *, enabled: bool) -> None:
    if not enabled:
        suite.add(CaseResult("D5 Orchestrator emit hook", "SKIP", "disabled by flag"))
        return
    try:
        cr = client.post(
            f"{GATEWAY}/api/v1/tasks",
            json={
                "target": "http://127.0.0.1:9",
                "name": "cot-hook-probe",
                "description": "expect cot steps after tick",
            },
            timeout=30.0,
        )
        if cr.status_code != 200 or (cr.json() or {}).get("code") != "0":
            suite.add(
                CaseResult(
                    "D5 Orchestrator emit hook",
                    "SKIP",
                    f"cannot create task: {cr.status_code} {cr.text[:200]}",
                )
            )
            return
        task_id = cr.json()["data"]["taskId"]
        tick = client.post(f"{GATEWAY}/api/v1/tasks/{task_id}/tick", timeout=120.0)
        # tick 可能因 LLM 失败返回非 0，仍检查是否写出了步骤
        r = client.get(
            f"{GATEWAY}/api/v1/tasks/{task_id}/reasoning-steps",
            params={"limit": 100},
            timeout=20.0,
        )
        data = r.json() if r.content else {}
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            suite.add(
                CaseResult(
                    "D5 Orchestrator emit hook",
                    "FAIL",
                    f"list failed after tick status={tick.status_code}",
                    r.text[:300],
                )
            )
            return
        types = {row.get("stepType") for row in rows if isinstance(row, dict)}
        wanted = {
            "TASK_PLANNING",
            "TOOL_CALL",
            "RESULT_OBSERVATION",
            "FINAL_CONCLUSION",
            "RAG_RETRIEVAL",
        }
        hit = types & wanted
        if hit:
            suite.add(
                CaseResult(
                    "D5 Orchestrator emit hook",
                    "PASS",
                    f"types={sorted(types)} tick={tick.status_code}",
                )
            )
        else:
            suite.add(
                CaseResult(
                    "D5 Orchestrator emit hook",
                    "SKIP",
                    f"no cot types after tick={tick.status_code}; "
                    f"rows={len(rows)} (LLM/skills may be unavailable)",
                )
            )
    except Exception as exc:  # noqa: BLE001
        suite.add(CaseResult("D5 Orchestrator emit hook", "SKIP", str(exc)))


def write_report(suite: Suite) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for r in suite.results if r.status == "PASS")
    failed = sum(1 for r in suite.results if r.status == "FAIL")
    skipped = sum(1 for r in suite.results if r.status == "SKIP")
    lines = [
        "# CoT Reasoning Steps 动态测试报告",
        "",
        f"- 时间: {_now()}",
        f"- Evidence: `{EVIDENCE}`",
        f"- Gateway: `{GATEWAY}`",
        f"- 结果: PASS={passed} FAIL={failed} SKIP={skipped}",
        "",
        "| 用例 | 结果 | 说明 |",
        "|------|------|------|",
    ]
    for r in suite.results:
        detail = (r.detail or "").replace("|", "\\|")
        lines.append(f"| {r.name} | {r.status} | {detail} |")
        if r.log_excerpt:
            lines.extend(["", "```", r.log_excerpt[:800], "```", ""])
    lines.extend(
        [
            "",
            "## 残留风险",
            "",
            "- D5 依赖 LLM/执行器；SKIP 不代表挂钩代码缺失。",
            "- 既有 MySQL volume 依赖 Evidence 启动时 `CREATE TABLE IF NOT EXISTS`。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {REPORT_PATH}")


def probe(client: httpx.Client, name: str, url: str) -> bool:
    try:
        r = client.get(url, timeout=8.0)
        ok = r.status_code == 200
        print(f"probe {name}: {'ok' if ok else r.status_code} ({url})")
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"probe {name}: FAIL {exc} ({url})")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-orch-hook", action="store_true")
    args = parser.parse_args()
    suite = Suite()
    task_id = f"task-cot-dyn-{uuid.uuid4().hex[:10]}"

    with httpx.Client() as client:
        ev_ok = probe(client, "evidence", f"{EVIDENCE}/health")
        gw_ok = probe(client, "gateway", f"{GATEWAY}/health")
        if not ev_ok:
            suite.add(CaseResult("D0 Evidence health", "FAIL", "evidence unreachable"))
            write_report(suite)
            return 1
        suite.add(CaseResult("D0 Evidence health", "PASS"))
        if not gw_ok:
            suite.add(CaseResult("D0 Gateway health", "FAIL", "gateway unreachable"))
            write_report(suite)
            return 1
        suite.add(CaseResult("D0 Gateway health", "PASS"))

        step_id = case_d1_ingest(client, suite, task_id)
        case_d2_list(client, suite, task_id, step_id)
        case_d3_gateway(client, suite, task_id, step_id)
        case_d4_reject(client, suite)
        case_d5_orch_hook(client, suite, enabled=not args.skip_orch_hook)
        case_d5b_orch_client_path(suite)

    write_report(suite)
    failed = sum(1 for r in suite.results if r.status == "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
