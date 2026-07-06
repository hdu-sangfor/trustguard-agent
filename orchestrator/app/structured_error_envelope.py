"""
结构化错误包（nf-structured-error-envelope）。

- Plan/契约层：复用 `PlanErrorEnvelope`（见 plan_models）序列化到 Trace / API。
- 执行派发层：将 dispatcher 返回的 status / 内层 error 映射为稳定 `code`，供客户端与 LLM 解析。
"""

from __future__ import annotations

from typing import Any

from app.plan_models import PlanErrorEnvelope


def plan_error_envelope_to_client_dict(env: PlanErrorEnvelope) -> dict[str, Any]:
    """JSON 可序列化 dict，供 Trace payload[\"structured_error\"] 或 HTTP 响应体嵌套。"""
    return env.model_dump(mode="json")


def structured_error_for_skill_dispatch(
    *,
    status: str,
    skill_id: str,
    inner_error: str | None = None,
    file_missing: bool = False,
) -> dict[str, Any]:
    """
    单次 skill 派发/落盘失败时的稳定错误结构（与 agent_tools 注入的 system_error_hint 并存）。
    """
    st = (status or "").strip().upper()
    inner = (inner_error or "").strip()
    code = _dispatch_status_to_code(st, inner, file_missing)
    details: dict[str, Any] = {
        "raw_status": status or "",
        "skill_id": skill_id or "",
    }
    if inner:
        details["inner_error"] = inner
    if file_missing:
        details["file_missing"] = True
    message = _default_message_for_code(code, skill_id=skill_id, inner_error=inner, file_missing=file_missing)
    return {
        "kind": "skill_dispatch",
        "code": code,
        "message": message,
        "details": details,
    }


def _dispatch_status_to_code(status_upper: str, inner_error: str, file_missing: bool) -> str:
    if file_missing and status_upper in ("OK", "SUCCESS", ""):
        return "ARTIFACT_FILE_MISSING"
    if inner_error == "MQ_POLL_TIMEOUT":
        return "MQ_POLL_TIMEOUT"
    if status_upper == "SKIPPED_EXECUTOR_DISABLED":
        return "SKIPPED_EXECUTOR_DISABLED"
    if status_upper == "SKIPPED_DUPLICATE_REQUEST":
        return "SKIPPED_DUPLICATE_REQUEST"
    if status_upper == "TIMEOUT":
        return "EXECUTION_TIMEOUT"
    if status_upper in ("FAILED", "ERROR"):
        return "EXECUTION_FAILED"
    if status_upper in ("OK", "SUCCESS"):
        return "OK"
    if status_upper:
        return status_upper
    return "UNKNOWN_EXECUTION_ERROR"


def _default_message_for_code(
    code: str,
    *,
    skill_id: str,
    inner_error: str,
    file_missing: bool,
) -> str:
    if code == "ARTIFACT_FILE_MISSING":
        return f"artifact_ref present but parsed artifact file missing for skill={skill_id}"
    if code == "MQ_POLL_TIMEOUT":
        return f"MQ poll timeout waiting for worker result (skill={skill_id})"
    if code == "SKIPPED_EXECUTOR_DISABLED":
        return f"executor disabled; skill={skill_id} not run"
    if code == "SKIPPED_DUPLICATE_REQUEST":
        return f"duplicate request_id; skill={skill_id} skipped"
    if code == "EXECUTION_TIMEOUT":
        return f"execution timeout (skill={skill_id})"
    if code == "EXECUTION_FAILED":
        return f"execution failed (skill={skill_id}); inner_error={inner_error or 'n/a'}"
    if code == "OK":
        return "ok"
    return f"skill={skill_id} code={code}"
