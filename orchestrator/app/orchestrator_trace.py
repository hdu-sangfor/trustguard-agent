"""
R7a：plan / compile / execution 轨迹读模型（HTTP 层拼装用）。
R7b：分页参数、字段裁剪、敏感信息脱敏（plan / compile / execution 摘要）。

compile 产物当前未写入 target_context 时，返回显式 not_persisted 占位。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException

from app.core.plan_business_validate import PLAN_LIST_VALIDATION_ERROR_CONTEXT_KEY
from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY
from app.core.task_store import ExecutionRecord

EXECUTION_TRACE_FIELD_ORDER = (
    "request_id",
    "task_id",
    "skill_id",
    "status",
    "artifact_ref",
    "artifact_refs_v1",
    "started_at",
    "finished_at",
    "worker_id",
    "todo_id",
)
EXECUTION_TRACE_FIELDS_ALLOWED: Set[str] = set(EXECUTION_TRACE_FIELD_ORDER)

_BEARER_IN_STRING_RE = re.compile(r"(?i)\bBearer\s+[\w\-\._~+/]+=*")
_COOKIE_HDR_IN_STRING_RE = re.compile(r"(?i)\bCookie:\s*\S+")


def orch_trace_api_token_expected() -> str:
    return (os.getenv("ORCH_TRACE_API_TOKEN") or "").strip()


def require_orch_trace_token_if_set(
    authorization: str | None,
    x_orch_trace_token: str | None,
) -> None:
    expected = orch_trace_api_token_expected()
    if not expected:
        return
    got = ""
    if authorization and authorization.lower().startswith("bearer "):
        got = authorization[7:].strip()
    elif x_orch_trace_token:
        got = x_orch_trace_token.strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="orch_trace_token_required")


def execution_record_to_dict(rec: ExecutionRecord) -> dict:
    return {
        "request_id": rec.request_id,
        "task_id": rec.task_id,
        "skill_id": rec.skill_id,
        "status": rec.status,
        "artifact_ref": rec.artifact_ref,
        "artifact_refs_v1": rec.artifact_refs_v1,
        "started_at": rec.started_at.isoformat() if rec.started_at else None,
        "finished_at": rec.finished_at.isoformat() if rec.finished_at else None,
        "worker_id": rec.worker_id,
        "todo_id": rec.todo_id,
    }


def trace_redact_sensitive_enabled() -> bool:
    return (os.getenv("ORCH_TRACE_REDACT_SENSITIVE", "true") or "").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def plan_content_max_chars() -> int:
    raw = (os.getenv("ORCH_TRACE_PLAN_CONTENT_MAX_CHARS") or "8192").strip() or "8192"
    try:
        v = int(raw)
    except ValueError:
        return 8192
    return max(256, min(v, 200_000))


def redact_string(s: str) -> str:
    s = _BEARER_IN_STRING_RE.sub("Bearer [REDACTED]", s)
    s = _COOKIE_HDR_IN_STRING_RE.sub("Cookie: [REDACTED]", s)
    return s


def _sensitive_dict_key(name: str) -> bool:
    n = name.lower()
    needles = (
        "password",
        "secret",
        "apikey",
        "api_key",
        "token",
        "cookie",
        "authorization",
        "set-cookie",
        "session",
    )
    return any(x in n for x in needles)


def redact_json_like(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            kn = str(k)
            if _sensitive_dict_key(kn):
                out[kn] = "[REDACTED]"
            else:
                out[kn] = redact_json_like(v)
        return out
    if isinstance(obj, list):
        return [redact_json_like(x) for x in obj]
    if isinstance(obj, str):
        return redact_string(obj)
    return obj


def parse_execution_fields_param(raw: Optional[str]) -> Optional[Set[str]]:
    if raw is None or not str(raw).strip():
        return None
    parts = {p.strip() for p in str(raw).split(",") if p.strip()}
    unknown = parts - EXECUTION_TRACE_FIELDS_ALLOWED
    if unknown:
        raise ValueError(f"unknown_execution_fields:{','.join(sorted(unknown))}")
    return parts


def shape_execution_dict(d: Dict[str, Any], fields: Optional[Set[str]]) -> Dict[str, Any]:
    if fields is None:
        return dict(d)
    return {k: d[k] for k in EXECUTION_TRACE_FIELD_ORDER if k in fields and k in d}


def finalize_execution_for_trace_response(
    d: Dict[str, Any],
    *,
    fields: Optional[Set[str]],
    redact: bool,
) -> Dict[str, Any]:
    shaped = shape_execution_dict(d, fields)
    if redact and trace_redact_sensitive_enabled():
        out = dict(shaped)
        ar = out.get("artifact_ref")
        if isinstance(ar, str):
            out["artifact_ref"] = redact_string(ar)
        refs = out.get("artifact_refs_v1")
        if isinstance(refs, list):
            out["artifact_refs_v1"] = [
                redact_string(x) if isinstance(x, str) else x for x in refs
            ]
        return out
    return shaped


def prepare_plan_section_api(
    plan_section: Dict[str, Any],
    *,
    omit_plan_content: bool,
    include_chunk_refs: bool,
    apply_redact: bool,
) -> Dict[str, Any]:
    section: Dict[str, Any] = json.loads(json.dumps(plan_section))
    lst = section.get("latest_plan_list")
    if isinstance(lst, dict):
        items = lst.get("items")
        if isinstance(items, list):
            max_pc = plan_content_max_chars()
            for it in items:
                if not isinstance(it, dict):
                    continue
                if omit_plan_content:
                    it["plan_content"] = "[omitted]"
                else:
                    pc = it.get("plan_content")
                    if isinstance(pc, str) and len(pc) > max_pc:
                        it["plan_content"] = pc[:max_pc] + "…[truncated]"
                if not include_chunk_refs:
                    it.pop("context_chunk_refs", None)
    if not include_chunk_refs:
        section["context_chunk_refs_flat"] = []
    if apply_redact and trace_redact_sensitive_enabled():
        section = redact_json_like(section)
    return section


def prepare_compile_section_api(
    compile_section: Dict[str, Any],
    *,
    apply_redact: bool,
) -> Dict[str, Any]:
    section: Dict[str, Any] = json.loads(json.dumps(compile_section))
    if apply_redact and trace_redact_sensitive_enabled():
        section = redact_json_like(section)
    return section


_TACTICAL_INCREMENTAL_KEYS = (
    "kind",
    "summary",
    "severity",
    "ref_id",
    "tool",
    "skill_id",
    "todo_id",
)


def prepare_tactical_incremental_section_api(
    raw: Any,
    *,
    apply_redact: bool,
    max_items: int = 200,
) -> List[Dict[str, Any]]:
    """
    从 target_context['_tactical_incremental_artifacts'] 生成 Trace / V1 管理面安全视图。
    """
    if not isinstance(raw, list):
        return []
    cap = max(1, min(int(max_items), 500))
    slice_ = raw[-cap:]
    out: List[Dict[str, Any]] = []
    for item in slice_:
        if not isinstance(item, dict):
            continue
        row = {k: item[k] for k in _TACTICAL_INCREMENTAL_KEYS if k in item and item[k] is not None}
        if row:
            out.append(row)
    if apply_redact and trace_redact_sensitive_enabled():
        return redact_json_like(out)  # type: ignore[return-value]
    return out


def clamp_execution_paging_limit(limit: int) -> int:
    return max(1, min(int(limit), 500))


def clamp_execution_paging_offset(offset: int) -> int:
    return max(0, min(int(offset), 10_000))


def _flatten_chunk_refs_from_plan_list(plan_list: Any) -> List[Dict[str, Any]]:
    if not isinstance(plan_list, dict):
        return []
    items = plan_list.get("items")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        refs = it.get("context_chunk_refs")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if isinstance(ref, dict):
                out.append(ref)
    return out


def plan_section_from_context(target_context: Dict[str, Any]) -> dict:
    raw = target_context.get(LATEST_PLAN_LIST_CONTEXT_KEY)
    return {
        "latest_plan_list": raw,
        "latest_plan_list_saved_at": target_context.get("_latest_plan_list_saved_at"),
        "plan_list_validation_error": target_context.get(PLAN_LIST_VALIDATION_ERROR_CONTEXT_KEY),
        "context_chunk_refs_flat": _flatten_chunk_refs_from_plan_list(raw),
    }


def compile_section_from_context(target_context: Dict[str, Any]) -> dict:
    trace = target_context.get("_latest_compile_trace")
    if trace is not None:
        entries = trace if isinstance(trace, list) else [trace]
        return {"persisted": True, "entries": entries}
    return {
        "persisted": False,
        "entries": [],
        "message": "compile trace not persisted in target_context in this build",
    }
