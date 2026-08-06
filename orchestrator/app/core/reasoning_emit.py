"""编排器侧 CoT 步骤写出：脱敏 + 组装 ReasoningStep + 发往 Evidence。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.clients.reasoning_client import emit_reasoning_step
from app.models import ReasoningStep
from app.orchestrator_trace import redact_json_like, redact_string, trace_redact_sensitive_enabled
from app.reasoning_steps import is_valid_status, is_valid_step_type, label_zh

_log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _maybe_redact(value: Any) -> Any:
    if not trace_redact_sensitive_enabled():
        return value
    if isinstance(value, str):
        return redact_string(value)
    return redact_json_like(value)


async def emit_cot_step(
    *,
    task_id: str,
    step_type: str,
    status: str,
    summary: str = "",
    payload: dict[str, Any] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_ms: int | None = None,
    step_id: str | None = None,
    source_module: str = "orchestrator",
) -> None:
    """
    写出一条 Reasoning Step。trace_id = task_id。
    非法 step_type/status 仅打日志并跳过，避免拖垮主路径。
    """
    tid = (task_id or "").strip()
    if not tid:
        return
    st = (step_type or "").strip().upper()
    if not is_valid_step_type(st):
        _log.warning("emit_cot_step skip unknown step_type=%s task_id=%s", st, tid)
        return
    status_u = (status or "").strip().upper()
    if not is_valid_status(status_u):
        _log.warning("emit_cot_step skip unknown status=%s task_id=%s", status_u, tid)
        return

    started = started_at or _now_iso()
    finished = finished_at
    if status_u in {"SUCCEEDED", "FAILED", "SKIPPED"} and not finished:
        finished = _now_iso()

    raw_payload = dict(payload or {})
    raw_payload.setdefault("source_module", source_module)
    raw_payload.setdefault("label_zh", label_zh(st))
    safe_payload = _maybe_redact(raw_payload)
    if not isinstance(safe_payload, dict):
        safe_payload = {"value": safe_payload}
    safe_summary = _maybe_redact(summary or "")
    if not isinstance(safe_summary, str):
        safe_summary = str(safe_summary)

    step = ReasoningStep(
        trace_id=tid,
        task_id=tid,
        step_id=step_id or f"rst-{uuid.uuid4().hex[:16]}",
        step_type=st,
        status=status_u,
        started_at=started,
        finished_at=finished,
        duration_ms=duration_ms,
        summary=safe_summary[:2000],
        payload=safe_payload,
    )
    await emit_reasoning_step(step)
