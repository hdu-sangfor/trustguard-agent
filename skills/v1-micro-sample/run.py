"""
V1 样板 Native Skill：Pydantic 入参 → TargetScope 校验 → 工作区落盘 → stderr S-06 → stdout 契约 JSON（raw_stdout 为 Agent 摘要）。

容器内：`/skill/micro_executor` + PYTHONPATH=/skill。
宿主机：仓库 `executor` 须在 `skills/v1-micro-sample` 的上两级目录之上（即标准 TrustGuard Agent 布局）。

**S-01**：`serialize_for_agent_stdout` 默认字符预算由 ENV **`MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV`**（字面量 **`MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS`**）控制，见 **`v1-env-config.md` §2.5**。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


def _import_micro_executor():
    """镜像内为顶层包 `micro_executor`；开发机为 `app.micro_executor`。"""
    here = Path(__file__).resolve().parent
    if (here / "micro_executor").is_dir():
        root = str(here)
        if root not in sys.path:
            sys.path.insert(0, root)
        return importlib.import_module("micro_executor")
    repo = here.parent.parent
    ex = repo / "executor"
    if not ex.is_dir():
        raise RuntimeError(
            "micro_executor not beside run.py and executor not found; "
            "use Docker image or standard repo layout",
        )
    root = str(ex)
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("app.micro_executor")


class V1MicroSampleParams(BaseModel):
    # extra="forbid"：reject unknown params to enforce strict contracts
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="ok", max_length=256)


def _fail(
    start: float,
    msg: str,
    *,
    stderr_extra: str = "",
) -> int:
    duration_ms = int((time.perf_counter() - start) * 1000)
    err_line = (stderr_extra or msg)[:4000]
    out = {
        "status": "FAILED",
        "parsed_artifacts": {"error": msg},
        "raw_stdout": "",
        "raw_stderr": err_line,
        "duration_ms": duration_ms,
    }
    print(json.dumps(out, ensure_ascii=False))
    if err_line:
        print(err_line, file=sys.stderr, flush=True)
    return 1


def main() -> int:
    start = time.perf_counter()
    raw = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.loads(raw) if (raw or "").strip() else {}
    except json.JSONDecodeError as e:
        return _fail(start, f"invalid json argv: {e}")

    if not isinstance(payload, dict):
        return _fail(start, "payload must be a JSON object")

    sdk = _import_micro_executor()
    task_id = str(payload.get("task_id") or "").strip()
    skill_id = str(payload.get("skill_id") or "").strip() or "v1-micro-sample"
    target = str(payload.get("target") or "").strip()
    allowed = payload.get("allowed_target")
    allowed_s = str(allowed).strip() if allowed else ""

    ctx_raw = payload.get("context")
    ctx: dict[str, Any] = dict(ctx_raw) if isinstance(ctx_raw, dict) else {}

    if not task_id:
        return _fail(start, "task_id required")
    if not target:
        return _fail(start, "target required")

    try:
        params_in = payload.get("params")
        params = V1MicroSampleParams.model_validate(
            params_in if isinstance(params_in, dict) else {},
        )
    except ValidationError as e:
        return _fail(start, f"params validation failed: {e}")

    try:
        sdk.TargetScopeValidator.validate(
            target,
            allowed_s or None,
            skill_id=skill_id,
        )
    except sdk.TargetScopeError as e:
        return _fail(start, f"target scope: {e}")

    ref = str(ctx.get("executor_artifact_ref") or "").strip()
    base_rel = str(ctx.get("executor_artifact_base") or "").strip()
    if not ref.startswith("wsref:"):
        return _fail(start, "context.executor_artifact_ref (wsref:…) required for V1 demo")
    if not base_rel:
        return _fail(start, "context.executor_artifact_base required")

    workspace = (os.getenv("WORKSPACE_ROOT") or "/data/workspace").strip()
    base_path = Path(workspace) / base_rel
    try:
        base_path.mkdir(parents=True, exist_ok=True)
        artifact_body: dict[str, Any] = {
            "skill_id": skill_id,
            "note": params.note,
            "target": target,
        }
        (base_path / "parsed.json").write_text(
            json.dumps(artifact_body, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        return _fail(start, f"workspace write failed: {e}")

    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()
    notice = sdk.build_artifact_notice(
        ref,
        skill_id=skill_id,
        type_tag="V1_DEMO",
        request_id=request_id,
    )
    print(notice, file=sys.stderr, flush=True)

    agent = sdk.AgentSummaryJSON(
        skill_id=skill_id,
        status="SUCCESS",
        artifact_ref=ref,
        summary=f"v1-micro-sample note={params.note!r}",
        highlights={"note": params.note},
    )
    # 默认预算：os.environ[sdk.MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV]，strip -> int -> max(256, n)；见 §2.5
    agent_line = sdk.serialize_for_agent_stdout(agent)

    duration_ms = int((time.perf_counter() - start) * 1000)
    envelope = {
        "status": "SUCCESS",
        "parsed_artifacts": {
            "artifact_ref": ref,
            "v1_micro_sample": True,
            **artifact_body,
        },
        "raw_stdout": agent_line,
        "raw_stderr": "",
        "duration_ms": duration_ms,
    }
    print(json.dumps(envelope, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
