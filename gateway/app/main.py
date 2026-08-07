from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import sys
import uuid
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.api.knowledge import router as knowledge_router
from app.audit import record_audit as _record_audit
from app.db import execute as _execute
from app.db import query as _query
from app.responses import fail, ok
from app.security.auth import (
    AUTH_TOKEN_TTL_SECONDS,
    CurrentUser,
    get_current_user,
    get_user_by_id,
    get_user_by_username,
    hash_password,
    issue_token,
    require_roles,
    verify_password,
)

log = logging.getLogger("trustguard.gateway")

ORCHESTRATOR_BASE_URL = os.getenv("ORCHESTRATOR_BASE_URL", "http://localhost:18081").rstrip("/")
EVIDENCE_BASE_URL = os.getenv("EVIDENCE_BASE_URL", "http://localhost:18103").rstrip("/")
EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://localhost:18102").rstrip("/")
SUPERVISOR_BASE_URL = os.getenv("SUPERVISOR_BASE_URL", "http://localhost:18082").rstrip("/")
MQ_BROKER_URL = os.getenv("MQ_BROKER_URL", "amqp://guest:guest@localhost:5672/")
KB_QDRANT_URL = os.getenv("KB_QDRANT_URL", "http://localhost:6333").rstrip("/")

START_TIME = datetime.now(timezone.utc)
PHASE_ORDER = ["RECON", "THREAT_MODEL", "VULN_SCAN", "EXPLOIT", "REPORT", "DONE"]
STATUS_VALUES = ["PENDING", "RUNNING", "PAUSED", "DONE", "FAILED", "CANCELLED"]
_EXECUTION_POLICY_SCHEMA_READY = False

app = FastAPI(title="TrustGuard Gateway", version="1.0.0")
app.include_router(knowledge_router)


def _scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
    rows = _query(sql, params)
    if not rows:
        return None
    return next(iter(rows[0].values()))


def _dt_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    if not text:
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_loads(value: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def _encode_sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {_json_dumps(data)}\n\n"


def _limit(value: int | None, default: int, max_value: int = 1000) -> int:
    if value is None or value <= 0:
        return default
    return min(value, max_value)


def _task_row_to_api(row: dict[str, Any]) -> dict[str, Any]:
    task_id = row.get("task_id") or ""
    return {
        "id": int(row.get("id") or 0),
        "taskId": task_id,
        "workflowId": "alert_triage" if str(task_id).startswith("at-") else "pentest",
        "name": row.get("name") or "",
        "target": row.get("target") or "",
        "description": row.get("description") or "",
        "status": row.get("status") or "PENDING",
        "currentPhase": row.get("current_phase") or "RECON",
        "createdAt": _dt_iso(row.get("created_at")) or "",
        "updatedAt": _dt_iso(row.get("updated_at")) or "",
        "executionPolicy": _json_loads(row.get("execution_policy"), {}),
    }


def _get_task_row(task_id: str) -> dict[str, Any] | None:
    rows = _query("SELECT * FROM tg_task WHERE task_id = %s", (task_id,))
    return rows[0] if rows else None


def _sync_task_state(task_id: str, state: dict[str, Any] | None) -> None:
    if not state:
        return
    status = str(state.get("status") or "").upper()
    phase = str(state.get("currentPhase") or state.get("current_phase") or "").upper()
    if status not in STATUS_VALUES:
        status = None
    if phase not in PHASE_ORDER:
        phase = None
    if not (status or phase):
        return

    # This function runs while the UI polls task state.  Updating updated_at on
    # every read turns it into a heartbeat, which makes completed/failed task
    # durations keep growing.  Only stamp it when persisted task state changes.
    row = _get_task_row(task_id)
    if not row:
        return
    current_status = str(row.get("status") or "").upper()
    current_phase = str(row.get("current_phase") or "").upper()
    status_changed = status is not None and status != current_status
    phase_changed = phase is not None and phase != current_phase
    if not (status_changed or phase_changed):
        return

    parts: list[str] = []
    params: list[Any] = []
    if status_changed:
        parts.append("status = %s")
        params.append(status)
    if phase_changed:
        parts.append("current_phase = %s")
        params.append(phase)
    parts.append("updated_at = NOW()")
    params.append(task_id)
    _execute(f"UPDATE tg_task SET {', '.join(parts)} WHERE task_id = %s", tuple(params))


def _triage_severity(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, min(5, int(value)))
    if isinstance(value, str):
        return {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}.get(value.strip().lower())
    return None


def _triage_task_to_api(state: dict[str, Any] | None, row: dict[str, Any] | None = None) -> dict[str, Any]:
    """Expose one stable camelCase contract to the triage UI.

    Internal graph state and the persisted result intentionally remain snake_case;
    all conversion is centralized here so list/detail/create endpoints agree.
    """
    state = state or {}
    row = row or {}
    result = state.get("result") if isinstance(state.get("result"), dict) else {}

    def from_state(name: str, default: Any = None) -> Any:
        value = state.get(name)
        return result.get(name, default) if value is None else value

    status = str(state.get("status") or row.get("status") or "PENDING").upper()
    if status not in STATUS_VALUES:
        status = "FAILED"
    confidence_value = from_state("confidence")
    confidence = float(confidence_value) if isinstance(confidence_value, (int, float)) and not isinstance(confidence_value, bool) else None
    alert = state.get("alert") if isinstance(state.get("alert"), dict) else {}
    raw_whitelists = state.get("whitelist_matches")
    if not isinstance(raw_whitelists, list):
        raw_whitelists = [{"id": item} for item in (from_state("matched_whitelists", []) or [])]
    raw_incidents = state.get("related_incidents")
    if not isinstance(raw_incidents, list):
        raw_incidents = []
    if raw_incidents and not isinstance(raw_incidents[0], dict):
        raw_incidents = [{"uuid": item} for item in raw_incidents]
    raw_actions = from_state("recommended_actions", []) or []
    raw_citations = from_state("rag_citations", []) or []
    finished_at = state.get("finished_at") or result.get("finished_at")
    if not finished_at and status in ("DONE", "FAILED"):
        finished_at = row.get("updated_at")
    rag_degraded = bool(state.get("rag_degraded", False))

    return {
        "taskId": state.get("task_id") or row.get("task_id") or "",
        "workflowId": "alert_triage",
        "alertUuid": state.get("alert_uuid") or row.get("target") or "",
        "status": status,
        "verdict": from_state("verdict"),
        "confidence": confidence,
        "severity": _triage_severity(from_state("severity")),
        "summary": str(from_state("summary", "") or ""),
        "reasoning": str(from_state("reasoning", "") or ""),
        "ragEnabled": bool(state.get("enable_rag", True)),
        "createdAt": _dt_iso(state.get("created_at") or row.get("created_at")) or "",
        "finishedAt": _dt_iso(finished_at),
        "alertSummary": {
            "name": alert.get("name") or alert.get("title") or "",
            "uuId": state.get("alert_uuid") or row.get("target") or "",
            "severity": _triage_severity(alert.get("severity")),
            "threatDefine": alert.get("threat_define"),
            "direction": alert.get("direction"),
            "proofType": alert.get("proof_type") or alert.get("proofType"),
            "proofSummary": alert.get("proof_summary") or alert.get("proofSummary") or "",
        } if alert else None,
        "matchedWhitelists": raw_whitelists,
        "relatedIncidents": [
            {
                "uuId": item.get("uuid") or item.get("uuId") or item.get("id") or "",
                "name": item.get("name") or item.get("title") or item.get("uuid") or item.get("uuId") or item.get("id") or "",
                "severity": _triage_severity(item.get("severity")),
            }
            for item in raw_incidents if isinstance(item, dict)
        ],
        "recommendedActions": [
            {
                "action": str(item.get("action") or ""),
                "label": str(item.get("action") or ""),
                "category": {
                    "auto": "safe_auto",
                    "manual_confirm": "manual_required",
                    "forbidden": "forbidden",
                }.get(str(item.get("execution_level") or "manual_confirm"), "manual_required"),
                "description": str(item.get("rationale") or ""),
            }
            for item in raw_actions if isinstance(item, dict)
        ],
        "warnings": list(state.get("warnings") or result.get("warnings") or []),
        "missingEvidence": list(from_state("missing_evidence", []) or []),
        "enrichmentEvidence": list(from_state("enrichment_evidence", []) or []),
        "ragCitations": raw_citations if isinstance(raw_citations, list) else [],
        "ragDegraded": rag_degraded,
        "ragNote": (
            next((str(item) for item in state.get("warnings", []) if str(item).startswith("RAG_")), None)
            if rag_degraded
            else None
        ),
        "errors": list(state.get("validation_errors") or []),
    }


def _triage_terminal_text(task_id: str, status: str, state: dict[str, Any] | None) -> str:
    if status != "DONE":
        return f"告警研判任务 {task_id} 已结束，状态：{status}。请查看执行轨迹定位原因。"
    state = state or {}
    result = state.get("result") if isinstance(state.get("result"), dict) else {}
    verdict = str(result.get("verdict") or state.get("verdict") or "insufficient_evidence")
    verdict_label = {
        "true_positive": "真实攻击",
        "false_positive": "误报",
        "suspicious": "可疑，需人工复核",
        "insufficient_evidence": "证据不足",
    }.get(verdict, verdict)
    confidence = result.get("confidence", state.get("confidence"))
    confidence_text = f"{float(confidence):.0%}" if isinstance(confidence, (int, float)) else "未提供"
    summary = str(result.get("summary") or state.get("summary") or "").strip()
    refs = result.get("xdr_evidence_refs") or result.get("xdrEvidenceRefs") or []
    ref_ids = [
        f"{item.get('source')}:{item.get('uuid')}"
        for item in refs[:8]
        if isinstance(item, dict) and item.get("source") and item.get("uuid")
    ]
    actions = result.get("recommended_actions") or result.get("recommendedActions") or []
    lines = [
        f"告警研判任务 {task_id} 已完成。",
        f"结论：{verdict_label}（{verdict}），置信度：{confidence_text}。",
    ]
    if summary:
        lines.append(f"判断摘要：{summary}")
    if ref_ids:
        lines.append("关键证据：" + "、".join(ref_ids))
    if actions:
        action_text = "；".join(
            str(item.get("action") or "") for item in actions[:3] if isinstance(item, dict)
        )
        if action_text:
            lines.append("建议动作（均需人工确认）：" + action_text)
    lines.append("以上为基于 XDR 证据的研判建议，Agent 未执行隔离、封禁或关闭告警等写操作。")
    return "\n".join(lines)


async def _get_alert_triage_state(task_id: str) -> dict[str, Any] | None:
    try:
        state = await _orch("GET", f"{ORCH_ALERT_TRIAGE_PATH}/tasks/{task_id}", timeout=10.0)
        return state if isinstance(state, dict) else None
    except Exception:
        try:
            context = await _evidence("GET", f"/internal/tasks/{task_id}/context")
            state = context.get("alert_triage") if isinstance(context, dict) else None
            return state if isinstance(state, dict) else None
        except Exception:
            return None


async def _run_alert_triage_in_background(task_id: str) -> None:
    """Run the long-lived workflow after the create response has returned."""
    try:
        _execute("UPDATE tg_task SET status = 'RUNNING', updated_at = NOW() WHERE task_id = %s", (task_id,))
        result = await _orch("POST", f"{ORCH_ALERT_TRIAGE_PATH}/tasks/{task_id}/run", timeout=300.0)
        _sync_task_state(task_id, result if isinstance(result, dict) else None)
    except Exception:
        log.exception("alert triage background run failed task_id=%s", task_id)
        _execute("UPDATE tg_task SET status = 'FAILED', updated_at = NOW() WHERE task_id = %s", (task_id,))


async def _orch(method: str, path: str, *, json_body: Any = None, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 30.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, f"{ORCHESTRATOR_BASE_URL}{path}", json=json_body, params=params, headers=headers)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()


async def _supervisor(
    method: str,
    path: str,
    *,
    json_body: Any = None,
    actor_id: str = "",
    timeout: float = 20.0,
) -> Any:
    headers = {"X-Actor-Id": actor_id} if actor_id else None
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method,
            f"{SUPERVISOR_BASE_URL}{path}",
            json=json_body,
            headers=headers,
        )
        if resp.status_code == 204:
            return None
        if resp.is_error:
            try:
                detail = resp.json().get("detail")
            except Exception:
                detail = resp.text
            raise HTTPException(status_code=resp.status_code, detail=detail or "Supervisor request failed")
        return resp.json() if resp.content else None


async def _open_supervisor_stream(
    path: str,
    *,
    json_body: Any,
    actor_id: str,
) -> tuple[httpx.AsyncClient, httpx.Response]:
    client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=None))
    try:
        request = client.build_request(
            "POST",
            f"{SUPERVISOR_BASE_URL}{path}",
            json=json_body,
            headers={"X-Actor-Id": actor_id},
        )
        response = await client.send(request, stream=True)
        if response.is_error:
            body = await response.aread()
            try:
                detail = json.loads(body).get("detail")
            except Exception:
                detail = body.decode("utf-8", errors="replace")
            await response.aclose()
            await client.aclose()
            raise HTTPException(
                status_code=response.status_code,
                detail=detail or "Supervisor stream request failed",
            )
        return client, response
    except Exception:
        await client.aclose()
        raise


async def _evidence(method: str, path: str, *, json_body: Any = None, params: dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, f"{EVIDENCE_BASE_URL}{path}", json=json_body, params=params)
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        if not resp.content:
            return None
        return resp.json()


async def _restore_task(row: dict[str, Any]) -> dict[str, Any] | None:
    task_id = row.get("task_id")
    payload = {
        "name": row.get("name"),
        "target": row.get("target"),
        "description": row.get("description"),
        "businessBackground": row.get("business_background"),
        "extraUserRequirements": row.get("extra_user_requirements"),
        "executionPolicy": _json_loads(row.get("execution_policy"), {}),
    }
    state = await _orch("POST", f"/v1/orchestrator/tasks/{task_id}/restore", json_body=payload, timeout=20.0)
    _sync_task_state(task_id, state)
    return state


async def _best_effort_restore(row: dict[str, Any]) -> None:
    try:
        await _restore_task(row)
    except Exception as exc:
        log.warning("orchestrator restore failed task_id=%s: %s", row.get("task_id"), exc)


def _user_to_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "userId": row.get("user_id") or "",
        "username": row.get("username") or "",
        "displayName": row.get("display_name") or "",
        "email": row.get("email") or "",
        "role": row.get("role") or "VIEWER",
        "status": row.get("status") or "ACTIVE",
        "lastLoginAt": _dt_iso(row.get("last_login_at")),
        "createdAt": _dt_iso(row.get("created_at")) or "",
        "updatedAt": _dt_iso(row.get("updated_at")) or "",
    }


class GatewayExecutionPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    allow_exploit: bool = Field(default=True, validation_alias=AliasChoices("allow_exploit", "allowExploit"))
    allow_destructive_actions: bool = Field(
        default=False,
        validation_alias=AliasChoices("allow_destructive_actions", "allowDestructiveActions"),
    )


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    target: str = Field(min_length=1)
    description: str | None = None
    business_background: str | None = Field(default=None, validation_alias=AliasChoices("business_background", "businessBackground"))
    extra_user_requirements: str | None = Field(default=None, validation_alias=AliasChoices("extra_user_requirements", "extraUserRequirements"))
    execution_policy: GatewayExecutionPolicy | None = Field(
        default=None,
        validation_alias=AliasChoices("execution_policy", "executionPolicy"),
    )


class TaskAgentDraftRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, validation_alias=AliasChoices("conversation_id", "conversationId"))
    workflow_id: str = Field(default="auto", validation_alias=AliasChoices("workflow_id", "workflowId"), max_length=64)


class TaskAgentConfirmRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    confirmation_token: str = Field(validation_alias=AliasChoices("confirmation_token", "confirmationToken"), min_length=1)
    start: bool = True
    max_ticks: int = Field(default=100, validation_alias=AliasChoices("max_ticks", "maxTicks"), ge=1, le=1000)
    idempotency_key: str | None = Field(default=None, validation_alias=AliasChoices("idempotency_key", "idempotencyKey"), max_length=128)


class TaskKnowledgeChunksRequest(BaseModel):
    chunk_ids: list[str] = Field(default_factory=list, max_length=20)


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    displayName: str | None = None
    email: str | None = None


class UserCreateRequest(BaseModel):
    username: str
    displayName: str | None = None
    email: str | None = None
    role: str | None = "VIEWER"
    password: str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    db_ok = True
    try:
        _query("SELECT 1 AS ok")
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "service": "gateway", "database": "up" if db_ok else "down"}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
    return fail(detail, code="HTTP_ERROR", status_code=exc.status_code)


def _ensure_execution_policy_schema() -> None:
    global _EXECUTION_POLICY_SCHEMA_READY
    if _EXECUTION_POLICY_SCHEMA_READY:
        return
    columns = _query("SHOW COLUMNS FROM tg_task LIKE 'execution_policy'")
    if not columns:
        try:
            _execute("ALTER TABLE tg_task ADD COLUMN execution_policy JSON NULL COMMENT 'structured execution policy'")
        except Exception:
            if not _query("SHOW COLUMNS FROM tg_task LIKE 'execution_policy'"):
                raise
    _EXECUTION_POLICY_SCHEMA_READY = True


async def _create_task_impl(
    req: CreateTaskRequest,
    *,
    actor: CurrentUser | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    _ensure_execution_policy_schema()
    task_id = task_id or "task-" + uuid.uuid4().hex
    name = (req.name or "").strip() or "未命名任务"
    target = req.target.strip()
    execution_policy = req.execution_policy or GatewayExecutionPolicy()
    row = _get_task_row(task_id)
    if row is None:
        try:
            _execute(
                """
                INSERT INTO tg_task
                  (task_id, name, description, business_background, extra_user_requirements,
                   execution_policy, target, status, current_phase, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING', 'RECON', NOW(), NOW())
                """,
                (
                    task_id,
                    name,
                    req.description,
                    req.business_background,
                    req.extra_user_requirements,
                    _json_dumps(execution_policy.model_dump()),
                    target,
                ),
            )
        except Exception:
            if _get_task_row(task_id) is None:
                raise
        row = _get_task_row(task_id)
    try:
        await _orch(
            "POST",
            "/v1/orchestrator/tasks",
            json_body={
                "taskId": task_id,
                "name": name,
                "target": target,
                "description": req.description,
                "businessBackground": req.business_background,
                "extraUserRequirements": req.extra_user_requirements,
                "executionPolicy": {
                    "allowExploit": execution_policy.allow_exploit,
                    "allowDestructiveActions": execution_policy.allow_destructive_actions,
                },
            },
            timeout=10.0,
        )
    except Exception as exc:
        log.warning("orchestrator create failed task_id=%s: %s", task_id, exc)
    if actor is not None:
        _record_audit("TASK_CREATED", actor.user_id, task_id, f"source=task-agent target={target}")
    return _task_row_to_api(row or {})


@app.post("/api/v1/tasks")
async def create_task(req: CreateTaskRequest) -> dict[str, Any]:
    return ok(await _create_task_impl(req))


@app.get("/api/v1/tasks")
def list_tasks(limit: int = 200) -> dict[str, Any]:
    rows = _query(
        "SELECT * FROM tg_task ORDER BY created_at DESC LIMIT %s",
        (_limit(limit, 200),),
    )
    return ok([_task_row_to_api(r) for r in rows])


@app.get("/api/v1/tasks/{task_id}", response_model=None)
async def get_task(task_id: str) -> dict[str, Any] | JSONResponse:
    row = _get_task_row(task_id)
    if not row:
        return fail("任务不存在", code="NOT_FOUND", data={"taskId": task_id})
    try:
        state = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}", timeout=5.0)
        _sync_task_state(task_id, state)
        row = _get_task_row(task_id) or row
    except Exception:
        pass
    return ok(_task_row_to_api(row))


@app.delete("/api/v1/tasks/{task_id}", response_model=None)
def delete_task(task_id: str) -> dict[str, Any] | JSONResponse:
    rows = _execute("DELETE FROM tg_task WHERE task_id = %s", (task_id,))
    if rows <= 0:
        return fail("任务不存在", code="NOT_FOUND", data={"taskId": task_id})
    return ok({"taskId": task_id, "deleted": True})


async def _run_lifecycle(
    task_id: str,
    action: str,
    max_ticks: int = 100,
    max_duration_seconds: int | None = None,
) -> JSONResponse | dict[str, Any]:
    row = _get_task_row(task_id)
    if not row:
        return fail("任务不存在", code="NOT_FOUND", data={"taskId": task_id})
    await _best_effort_restore(row)
    params: dict[str, Any] = {}
    if action == "run":
        params = {"max_ticks": max_ticks}
        if max_duration_seconds is not None:
            params["max_duration_seconds"] = max_duration_seconds
    try:
        state = await _orch("POST", f"/v1/orchestrator/tasks/{task_id}/{action}", params=params, timeout=30.0 if action != "run" else 45.0)
        _sync_task_state(task_id, state)
        if action in ("tick", "run", "resume") and not state:
            _execute("UPDATE tg_task SET status = 'RUNNING', updated_at = NOW() WHERE task_id = %s", (task_id,))
        if action == "stop":
            _execute("UPDATE tg_task SET status = 'PAUSED', updated_at = NOW() WHERE task_id = %s", (task_id,))
        return ok(None)
    except Exception as exc:
        log.warning("orchestrator %s failed task_id=%s: %s", action, task_id, exc)
        _execute("UPDATE tg_task SET status = 'FAILED', updated_at = NOW() WHERE task_id = %s", (task_id,))
        return fail(f"编排器调用失败: {exc}", code="ORCHESTRATOR_ERROR")


@app.post("/api/v1/tasks/{task_id}/tick", response_model=None)
async def tick_task(task_id: str) -> JSONResponse | dict[str, Any]:
    return await _run_lifecycle(task_id, "tick")


@app.post("/api/v1/tasks/{task_id}/run", response_model=None)
async def run_task(
    task_id: str,
    maxTicks: int = Query(100),
    max_duration_seconds: int | None = Query(None),
) -> JSONResponse | dict[str, Any]:
    return await _run_lifecycle(task_id, "run", max_ticks=maxTicks, max_duration_seconds=max_duration_seconds)


@app.post("/api/v1/tasks/{task_id}/stop", response_model=None)
async def stop_task(task_id: str) -> JSONResponse | dict[str, Any]:
    return await _run_lifecycle(task_id, "stop")


@app.post("/api/v1/tasks/{task_id}/resume", response_model=None)
async def resume_task(
    task_id: str,
    maxTicks: int = Query(100),
    max_duration_seconds: int | None = Query(None),
) -> JSONResponse | dict[str, Any]:
    result = await _run_lifecycle(task_id, "resume")
    if isinstance(result, dict) and maxTicks > 0:
        return await _run_lifecycle(task_id, "run", max_ticks=maxTicks, max_duration_seconds=max_duration_seconds)
    return result


@app.post("/api/v1/task-agent/draft")
async def task_agent_draft(
    req: TaskAgentDraftRequest,
    user: CurrentUser = Depends(require_roles("ADMIN", "OPERATOR")),
) -> dict[str, Any]:
    body = await _supervisor(
        "POST",
        "/v1/task-agent/draft",
        json_body={
            "message": req.message,
            "conversationId": req.conversation_id,
            "workflowId": req.workflow_id,
        },
        actor_id=user.user_id,
    )
    _record_audit(
        "TASK_AGENT_DRAFT",
        user.user_id,
        str((body or {}).get("draftId") or "platform"),
        f"status={(body or {}).get('status')}",
    )
    return ok(body)


@app.post("/api/v1/task-agent/draft/stream")
async def task_agent_draft_stream(
    req: TaskAgentDraftRequest,
    user: CurrentUser = Depends(require_roles("ADMIN", "OPERATOR")),
) -> StreamingResponse:
    client, response = await _open_supervisor_stream(
        "/v1/task-agent/draft/stream",
        json_body={
            "message": req.message,
            "conversationId": req.conversation_id,
            "workflowId": req.workflow_id,
        },
        actor_id=user.user_id,
    )
    _record_audit(
        "TASK_AGENT_DRAFT_STREAM",
        user.user_id,
        "platform",
        "status=started",
    )

    async def relay():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/task-agent/conversations")
async def task_agent_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    user: CurrentUser = Depends(require_roles("ADMIN", "OPERATOR")),
) -> dict[str, Any]:
    body = await _supervisor(
        "GET",
        f"/v1/conversations?limit={limit}",
        actor_id=user.user_id,
    )
    return ok(body)


@app.get("/api/v1/task-agent/conversations/{conversation_id}")
async def task_agent_conversation(
    conversation_id: str,
    user: CurrentUser = Depends(require_roles("ADMIN", "OPERATOR")),
) -> dict[str, Any]:
    body = await _supervisor(
        "GET",
        f"/v1/conversations/{conversation_id}",
        actor_id=user.user_id,
    )
    return ok(body)


@app.post("/api/v1/task-agent/confirm")
async def task_agent_confirm(
    req: TaskAgentConfirmRequest,
    user: CurrentUser = Depends(require_roles("ADMIN", "OPERATOR")),
) -> dict[str, Any]:
    idem = (req.idempotency_key or "").strip() or hashlib.sha256(
        req.confirmation_token.encode()
    ).hexdigest()[:32]

    consumed = await _supervisor(
        "POST",
        "/v1/task-agent/drafts/consume",
        json_body={"confirmationToken": req.confirmation_token, "idempotencyKey": idem},
        actor_id=user.user_id,
    )
    draft = (consumed or {}).get("draft") or {}
    workflow_id = str(
        (consumed or {}).get("workflowId")
        or draft.get("workflowId")
        or draft.get("workflow_id")
        or "pentest"
    ).strip().lower()
    if workflow_id == "alert_triage":
        return await _confirm_alert_triage_draft(consumed or {}, req, user, idem)
    target = str(draft.get("target") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="Supervisor draft is missing target")
    create_req = CreateTaskRequest(
        name=str(draft.get("name") or "自然语言渗透测试"),
        target=target,
        description=str(draft.get("description") or ""),
        business_background=str(draft.get("businessBackground") or draft.get("business_background") or ""),
        extra_user_requirements=str(draft.get("extraUserRequirements") or draft.get("extra_user_requirements") or ""),
        execution_policy=GatewayExecutionPolicy(
            allowExploit=bool(draft.get("allowExploit") or draft.get("allow_exploit")),
            # Destructive execution is not enabled by the natural-language
            # workflow in this version, even when requested in prose.
            allowDestructiveActions=False,
        ),
    )
    draft_id = str((consumed or {}).get("draftId") or "")
    if not draft_id:
        raise HTTPException(status_code=400, detail="Supervisor response is missing draftId")
    deterministic_task_id = "task-" + hashlib.sha256(draft_id.encode()).hexdigest()[:32]
    task_id = str((consumed or {}).get("taskId") or deterministic_task_id)
    task = await _create_task_impl(create_req, actor=user, task_id=task_id)
    task_id = str(task.get("taskId") or "")
    if str((consumed or {}).get("confirmationState") or "") != "COMPLETED":
        await _supervisor(
            "POST",
            "/v1/task-agent/drafts/complete",
            json_body={"draftId": draft_id, "idempotencyKey": idem, "taskId": task_id},
            actor_id=user.user_id,
        )
    activity = [
        {
            "id": "confirm-1",
            "kind": "tool",
            "title": "创建渗透测试任务",
            "detail": f"Gateway 已创建 {task_id}",
            "status": "done",
            "timestamp": _now_iso(),
        }
    ]
    should_start = req.start and str(task.get("status") or "").upper() not in {"RUNNING", "DONE"}
    if should_start:
        max_duration = int(draft.get("maxDurationSeconds") or draft.get("max_duration_seconds") or 900)
        result = await _run_lifecycle(
            task_id,
            "run",
            max_ticks=req.max_ticks,
            max_duration_seconds=max_duration,
        )
        if isinstance(result, JSONResponse):
            raise HTTPException(status_code=502, detail="任务已创建，但启动失败")
        activity.append(
            {
                "id": "confirm-2",
                "kind": "progress",
                "title": "启动 Pentest Workflow",
                "detail": "Orchestrator 已接管任务；后续阶段和工具事件会持续回到当前对话。",
                "status": "running",
                "timestamp": _now_iso(),
            }
        )
    conversation_id = str((consumed or {}).get("conversationId") or "")
    started = bool(req.start and (should_start or str(task.get("status") or "").upper() in {"RUNNING", "DONE"}))
    assistant_text = (
        f"任务 {task_id} 已创建并启动。我会持续把 Orchestrator 的阶段和工具事件写入这段对话。"
        if started
        else f"任务 {task_id} 已创建，当前尚未启动。"
    )
    persisted_message = {
        "id": f"confirm-{draft_id}",
        "role": "assistant",
        "text": assistant_text,
        "activities": activity,
        "taskId": task_id,
        "taskStatus": str(task.get("status") or "PENDING"),
        "createdAt": _now_iso(),
    }
    if conversation_id:
        try:
            persisted_message = await _supervisor(
                "POST",
                f"/v1/conversations/{conversation_id}/messages",
                json_body=persisted_message,
                actor_id=user.user_id,
                timeout=5.0,
            )
        except Exception:
            log.exception("failed to persist task-agent confirmation conversation_id=%s", conversation_id)
    payload = {
        "conversationId": conversation_id,
        "draftId": (consumed or {}).get("draftId"),
        "task": task,
        "started": started,
        "activities": activity,
        "message": persisted_message,
    }
    _record_audit("TASK_AGENT_CONFIRMED", user.user_id, task_id, f"start={req.start}")
    return ok(payload)


async def _confirm_alert_triage_draft(
    consumed: dict[str, Any],
    req: TaskAgentConfirmRequest,
    user: CurrentUser,
    idempotency_key: str,
) -> dict[str, Any]:
    draft = consumed.get("draft") if isinstance(consumed.get("draft"), dict) else {}
    alert_uuid = str(draft.get("alertUuid") or draft.get("alert_uuid") or "").strip()
    if not alert_uuid:
        raise HTTPException(status_code=400, detail="Supervisor triage draft is missing alertUuid")
    draft_id = str(consumed.get("draftId") or "").strip()
    if not draft_id:
        raise HTTPException(status_code=400, detail="Supervisor response is missing draftId")
    task_id = str(consumed.get("taskId") or "").strip()
    if not task_id:
        task_id = "at-" + hashlib.sha256(draft_id.encode()).hexdigest()[:32]

    request = CreateAlertTriageRequest(
        alert_uuid=alert_uuid,
        scenario_id=draft.get("scenarioId") or draft.get("scenario_id"),
        enable_rag=bool(draft.get("enableRag") or draft.get("enable_rag")),
        caller_notes=str(draft.get("callerNotes") or draft.get("caller_notes") or ""),
    )
    task: dict[str, Any]
    if str(consumed.get("confirmationState") or "").upper() == "COMPLETED" and task_id:
        # A retry after completion must return the durable result, rather than
        # rebuilding an empty create response from the original draft.
        existing_state = await _get_alert_triage_state(task_id)
        existing_row = _get_task_row(task_id)
        task = _triage_task_to_api(existing_state, existing_row) if existing_state else await _create_alert_triage_task_impl(
            request,
            task_id=task_id,
            auto_start=req.start,
        )
    else:
        task = await _create_alert_triage_task_impl(
            request,
            task_id=task_id,
            auto_start=req.start,
        )
    if str(consumed.get("confirmationState") or "") != "COMPLETED":
        await _supervisor(
            "POST",
            "/v1/task-agent/drafts/complete",
            json_body={
                "draftId": draft_id,
                "idempotencyKey": idempotency_key,
                "taskId": task_id,
            },
            actor_id=user.user_id,
        )

    activity = [
        {
            "id": "confirm-1",
            "kind": "tool",
            "title": "创建告警研判任务",
            "detail": f"Gateway 已创建 {task_id}",
            "status": "done",
            "timestamp": _now_iso(),
        }
    ]
    if req.start:
        activity.append(
            {
                "id": "confirm-2",
                "kind": "progress",
                "title": "启动 Alert Triage Workflow",
                "detail": "Orchestrator 已接管任务，将查询 XDR 证据并生成可追溯研判结论。",
                "status": "running",
                "timestamp": _now_iso(),
            }
        )
    conversation_id = str(consumed.get("conversationId") or "")
    assistant_text = (
        f"告警研判任务 {task_id} 已创建并启动。"
        if req.start
        else f"告警研判任务 {task_id} 已创建，当前尚未启动。"
    )
    persisted_message: dict[str, Any] = {
        "id": f"confirm-{draft_id}",
        "role": "assistant",
        "text": assistant_text,
        "activities": activity,
        "taskId": task_id,
        "taskStatus": str(task.get("status") or "PENDING"),
        "createdAt": _now_iso(),
    }
    if conversation_id:
        try:
            persisted_message = await _supervisor(
                "POST",
                f"/v1/conversations/{conversation_id}/messages",
                json_body=persisted_message,
                actor_id=user.user_id,
                timeout=5.0,
            )
        except Exception:
            log.exception(
                "failed to persist alert-triage confirmation conversation_id=%s",
                conversation_id,
            )
    _record_audit("TASK_AGENT_TRIAGE_CONFIRMED", user.user_id, task_id, f"start={req.start}")
    return ok(
        {
            "conversationId": conversation_id,
            "draftId": draft_id,
            "workflowId": "alert_triage",
            "task": task,
            "started": req.start,
            "activities": activity,
            "message": persisted_message,
        }
    )


@app.get("/api/v1/tasks/{task_id}/run-status")
async def run_status(task_id: str) -> dict[str, Any]:
    try:
        data = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}/run-status", timeout=8.0)
    except Exception:
        data = {"taskId": task_id, "running": False, "error": "orchestrator unavailable"}
    return ok(data)


@app.get("/api/v1/tasks/{task_id}/events")
async def task_events(task_id: str, limit: int = 500) -> dict[str, Any]:
    try:
        raw = await _evidence("GET", f"/internal/tasks/{task_id}/events", params={"limit": _limit(limit, 500)})
        data = [
            {
                "eventId": e.get("event_id"),
                "taskId": e.get("task_id"),
                "timestamp": e.get("timestamp"),
                "eventType": e.get("event_type"),
                "sourceModule": e.get("source_module"),
                "payload": e.get("payload"),
                "runStartedAt": e.get("run_started_at"),
                "runFinishedAt": e.get("run_finished_at"),
                "runDurationMs": e.get("run_duration_ms"),
            }
            for e in (raw or [])
        ]
    except Exception:
        rows = _query(
            """
            SELECT event_id, task_id, ts, event_type, source_module, payload,
                   run_started_at, run_finished_at, run_duration_ms
            FROM tg_trace_events
            WHERE task_id = %s
            ORDER BY ts ASC, id ASC
            LIMIT %s
            """,
            (task_id, _limit(limit, 500)),
        )
        data = [
            {
                "eventId": r.get("event_id"),
                "taskId": r.get("task_id"),
                "timestamp": _dt_iso(r.get("ts")),
                "eventType": r.get("event_type"),
                "sourceModule": r.get("source_module"),
                "payload": _json_loads(r.get("payload")),
                "runStartedAt": r.get("run_started_at") or "",
                "runFinishedAt": r.get("run_finished_at") or "",
                "runDurationMs": r.get("run_duration_ms") or 0,
            }
            for r in rows
        ]
    return ok(data or [])


@app.get("/api/v1/tasks/{task_id}/reasoning-steps")
async def task_reasoning_steps(task_id: str, limit: int = 500) -> dict[str, Any]:
    """结构化 CoT 推理步骤列表（Issue #136）；与 /events 运维轨迹并行。"""
    try:
        raw = await _evidence(
            "GET",
            f"/internal/tasks/{task_id}/reasoning-steps",
            params={"limit": _limit(limit, 500)},
        )
        data = [
            {
                "traceId": e.get("trace_id"),
                "taskId": e.get("task_id"),
                "stepId": e.get("step_id"),
                "stepType": e.get("step_type"),
                "status": e.get("status"),
                "startedAt": e.get("started_at"),
                "finishedAt": e.get("finished_at"),
                "durationMs": e.get("duration_ms"),
                "summary": e.get("summary") or "",
                "payload": e.get("payload") if isinstance(e.get("payload"), dict) else {},
            }
            for e in (raw or [])
        ]
    except Exception:
        rows = _query(
            """
            SELECT task_id, trace_id, step_id, step_type, status,
                   started_at, finished_at, duration_ms, summary, payload
            FROM tg_reasoning_steps
            WHERE task_id = %s
            ORDER BY COALESCE(started_at, created_at) ASC, id ASC
            LIMIT %s
            """,
            (task_id, _limit(limit, 500)),
        )
        data = [
            {
                "traceId": r.get("trace_id") or r.get("task_id"),
                "taskId": r.get("task_id"),
                "stepId": r.get("step_id"),
                "stepType": r.get("step_type"),
                "status": r.get("status"),
                "startedAt": r.get("started_at") or "",
                "finishedAt": r.get("finished_at") or "",
                "durationMs": r.get("duration_ms"),
                "summary": r.get("summary") or "",
                "payload": _json_loads(r.get("payload")),
            }
            for r in rows
        ]
    return ok(data or [])


@app.get("/api/v1/tasks/{task_id}/events/stream")
async def task_events_stream(
    task_id: str,
    user: CurrentUser = Depends(get_current_user),
    conversationId: str | None = Query(default=None, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
) -> StreamingResponse:
    if not _get_task_row(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")

    def is_meaningful(item: dict[str, Any]) -> bool:
        event_type = str(item.get("eventType") or "").upper()
        return (
            event_type.startswith("PHASE_")
            or event_type.startswith("SKILL_")
            or event_type.startswith("PLAN_")
            or event_type in {
                "KNOWLEDGE_INJECTED",
                "LOOP_BREAK",
                "TASK_PAUSED",
                "TASK_RESUMED",
                "EXECUTION_BLOCKED",
            }
        )

    def event_payload(item: dict[str, Any]) -> dict[str, Any]:
        payload = item.get("payload")
        return payload if isinstance(payload, dict) else {}

    def project_event(item: dict[str, Any]) -> dict[str, Any]:
        payload = event_payload(item)
        return {
            "eventType": item.get("eventType"),
            "sourceModule": item.get("sourceModule"),
            "timestamp": item.get("timestamp"),
            "phase": payload.get("phase") or payload.get("previous_phase"),
            "skillId": payload.get("skill_id") or payload.get("skillId"),
            "status": payload.get("status"),
            "detail": str(
                payload.get("message")
                or payload.get("reason")
                or payload.get("detail")
                or payload.get("next_phase")
                or ""
            )[:300],
        }

    async def progress_reply(
        task: dict[str, Any],
        recent: list[dict[str, Any]],
        total_steps: int,
    ) -> dict[str, Any]:
        phase = str(task.get("currentPhase") or "RECON")
        completed_skills = list(OrderedDict.fromkeys(
            str(event_payload(item).get("skill_id") or "")
            for item in recent
            if str(item.get("eventType") or "").upper() == "SKILL_COMPLETED"
            and str(event_payload(item).get("skill_id") or "")
        ))
        blocked = any(
            any(token in str(item.get("eventType") or "").upper() for token in ("FAILED", "BLOCK", "REJECT", "BUDGET"))
            for item in recent
        )
        skill_text = "、".join(completed_skills[-4:]) if completed_skills else "阶段分析"
        fallback = (
            f"阶段性进度：当前处于 {phase}，累计记录 {total_steps} 个执行步骤。"
            f"最近完成了 {skill_text}；{'存在阻塞或失败事件，正在调整后续计划' if blocked else '暂未形成可确认漏洞，正在继续验证'}。"
        )
        try:
            summary = await _supervisor(
                "POST",
                "/v1/task-agent/progress-summary",
                json_body={
                    "taskId": task_id,
                    "taskStatus": task.get("status") or "RUNNING",
                    "currentPhase": phase,
                    "totalSteps": total_steps,
                    "recentEvents": [project_event(item) for item in recent[-20:]],
                    "fallbackMessage": fallback,
                },
                actor_id=user.user_id,
                timeout=15.0,
            )
            text = str((summary or {}).get("assistantMessage") or fallback)
        except Exception:
            log.exception("failed to generate task progress summary task_id=%s", task_id)
            text = fallback
        checkpoint = str(recent[-1].get("eventId") or hashlib.sha256(
            _json_dumps(project_event(recent[-1])).encode()
        ).hexdigest()[:20])
        message = {
            "id": f"task-{task_id}-progress-{checkpoint}"[:160],
            "role": "assistant",
            "text": text,
            "activities": [],
            "taskId": task_id,
            "taskStatus": str(task.get("status") or "RUNNING"),
            "createdAt": _now_iso(),
        }
        if conversationId:
            try:
                message = await _supervisor(
                    "POST",
                    f"/v1/conversations/{conversationId}/messages",
                    json_body=message,
                    actor_id=user.user_id,
                    timeout=5.0,
                )
            except Exception:
                log.exception("failed to persist task progress reply task_id=%s", task_id)
        return message

    async def events():
        seen: set[str] = set()
        seen_reasoning: set[str] = set()
        last_state = ""
        last_summary_phase = ""
        pending_summary_events: list[dict[str, Any]] = []
        first_poll = True
        summary_steps = max(5, int(os.getenv("TASK_AGENT_PROGRESS_SUMMARY_STEPS") or "10"))
        last_heartbeat = asyncio.get_running_loop().time()
        while True:
            event_response = await task_events(task_id, 500)
            event_items = event_response.get("data") or []
            for index, item in enumerate(event_items):
                event_id = str(item.get("eventId") or f"{item.get('timestamp')}:{item.get('eventType')}:{index}")
                if event_id in seen:
                    continue
                seen.add(event_id)
                if is_meaningful(item):
                    pending_summary_events.append(item)
                yield _encode_sse("event", item)

            # ReasoningStep is the durable, user-facing reasoning trace. Keep it
            # parallel to operational TraceEvent instead of projecting one into
            # the other, so audit semantics and replay remain unambiguous.
            try:
                reasoning_response = await task_reasoning_steps(task_id, 500)
                reasoning_items = reasoning_response.get("data") or []
            except Exception:
                reasoning_items = []
            for index, item in enumerate(reasoning_items):
                step_id = str(item.get("stepId") or f"{item.get('startedAt')}:{item.get('stepType')}:{index}")
                if step_id in seen_reasoning:
                    continue
                seen_reasoning.add(step_id)
                yield _encode_sse("reasoning", item)

            try:
                state = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}", timeout=5.0)
                _sync_task_state(task_id, state)
            except Exception:
                pass
            row = _get_task_row(task_id) or {}
            task = _task_row_to_api(row)
            state_signature = f"{task.get('status')}:{task.get('currentPhase')}"
            if state_signature != last_state:
                last_state = state_signature
                yield _encode_sse("status", task)

            if str(task.get("status") or "").upper() in {"DONE", "FAILED", "CANCELLED"}:
                status = str(task.get("status") or "").upper()
                phase = str(task.get("currentPhase") or "")
                detail = ""
                if status == "FAILED":
                    for item in reversed(event_items):
                        event_type = str(item.get("eventType") or "").upper()
                        if "FAIL" not in event_type and "ERROR" not in event_type:
                            continue
                        payload = item.get("payload") or {}
                        detail = str(payload.get("message") or payload.get("reason") or payload.get("detail") or "").strip()[:500]
                        if detail:
                            break
                if status == "DONE":
                    if task_id.startswith("at-"):
                        triage_state = await _get_alert_triage_state(task_id)
                        text = _triage_terminal_text(task_id, status, triage_state)
                    else:
                        text = f"渗透测试任务 {task_id} 已完成。Pentest Workflow 已执行完毕，可以打开报告中心查看测试结果。"
                elif status == "FAILED":
                    text = f"渗透测试任务 {task_id} 执行失败，停止在 {phase or '当前'} 阶段。"
                    if detail:
                        text += f" 原因：{detail}"
                    text += " 你可以查看执行轨迹定位失败步骤。"
                else:
                    text = f"渗透测试任务 {task_id} 已取消。"
                terminal_message = {
                    "id": f"task-{task_id}-terminal",
                    "role": "assistant",
                    "text": text,
                    "activities": [],
                    "taskId": task_id,
                    "taskStatus": status,
                    "createdAt": _now_iso(),
                }
                if conversationId:
                    try:
                        terminal_message = await _supervisor(
                            "POST",
                            f"/v1/conversations/{conversationId}/messages",
                            json_body=terminal_message,
                            actor_id=user.user_id,
                            timeout=5.0,
                        )
                    except Exception:
                        log.exception("failed to persist terminal task reply task_id=%s", task_id)
                yield _encode_sse(
                    "done",
                    {"taskId": task_id, "status": status, "message": terminal_message},
                )
                return

            phase = str(task.get("currentPhase") or "")
            phase_changed = bool(last_summary_phase and phase and phase != last_summary_phase)
            should_summarize = bool(pending_summary_events) and (
                len(pending_summary_events) >= summary_steps
                or phase_changed
                or (first_poll and len(pending_summary_events) >= summary_steps)
            )
            if should_summarize:
                message = await progress_reply(task, pending_summary_events, len(seen))
                yield _encode_sse("assistant", message)
                pending_summary_events = []
            if phase:
                last_summary_phase = phase
            first_poll = False

            now = asyncio.get_running_loop().time()
            if now - last_heartbeat >= 15.0:
                last_heartbeat = now
                yield ": keep-alive\n\n"
            await asyncio.sleep(1.5)

    _record_audit("TASK_EVENT_STREAM_OPENED", user.user_id, task_id, "")
    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/v1/tasks/{task_id}/knowledge-chunks:batchGet", response_model=None)
async def task_knowledge_chunks(
    task_id: str,
    req: TaskKnowledgeChunksRequest,
    request: Request,
) -> dict[str, Any] | JSONResponse:
    """按任务读取已物化的 RAG 知识片段，仅返回日志展示所需的有界预览。"""
    if not _get_task_row(task_id):
        return fail("任务不存在", code="NOT_FOUND", data={"taskId": task_id})

    chunk_ids: list[str] = []
    for raw in req.chunk_ids:
        chunk_id = str(raw).strip()
        if not re.fullmatch(r"chk-[a-f0-9]{32}", chunk_id):
            continue
        if chunk_id not in chunk_ids:
            chunk_ids.append(chunk_id)
    if not chunk_ids:
        return ok({"chunks": [], "missingChunkIds": []})

    headers: dict[str, str] = {}
    tenant_id = (request.headers.get("X-Tenant-Id") or "").strip()
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    try:
        data = await _orch(
            "POST",
            f"/v1/orchestrator/tasks/{task_id}/chunks:batchGet",
            json_body={"chunk_ids": chunk_ids},
            headers=headers or None,
            timeout=10.0,
        )
    except Exception as exc:
        log.warning("knowledge chunk read failed task_id=%s: %s", task_id, exc)
        return fail("知识片段暂时不可用", code="KNOWLEDGE_CHUNK_UNAVAILABLE")

    records = data.get("chunks") if isinstance(data, dict) else {}
    records = records if isinstance(records, dict) else {}
    chunks: list[dict[str, Any]] = []
    missing: list[str] = []
    preview_limit = 6000
    for chunk_id in chunk_ids:
        record = records.get(chunk_id)
        if not isinstance(record, dict):
            missing.append(chunk_id)
            continue
        meta = record.get("meta")
        content = record.get("content")
        if not isinstance(meta, dict) or not isinstance(content, dict):
            missing.append(chunk_id)
            continue
        if meta.get("chunk_type") != "rag_knowledge":
            missing.append(chunk_id)
            continue

        text = str(content.get("text") or "")
        source_ref = content.get("source_ref")
        source_ref = source_ref if isinstance(source_ref, dict) else {}
        metadata = content.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        chunks.append(
            {
                "chunkId": chunk_id,
                "title": str(content.get("title") or "").strip(),
                "filename": str(content.get("filename") or "").strip(),
                "pageNo": content.get("page_no"),
                "preview": text[:preview_limit],
                "textLength": len(text),
                "truncated": len(text) > preview_limit,
                "sourceType": str(source_ref.get("source_type") or "").strip(),
                "sourceUri": str(source_ref.get("source_uri") or "").strip(),
                "scope": str(source_ref.get("scope") or "").strip(),
                "contentType": str(metadata.get("content_type") or "").strip(),
            }
        )
    return ok({"chunks": chunks, "missingChunkIds": missing})


@app.get("/api/v1/tasks/{task_id}/observation")
async def task_observation(task_id: str, artifactsSummaryLimit: int = 500) -> dict[str, Any]:
    row = _get_task_row(task_id)
    context: dict[str, Any] = {}
    artifacts: list[dict[str, Any]] = []
    try:
        ctx = await _evidence("GET", f"/internal/tasks/{task_id}/context")
        if isinstance(ctx, dict):
            context = ctx
    except Exception:
        pass
    try:
        items = await _evidence("GET", f"/internal/tasks/{task_id}/artifacts-summary", params={"limit": artifactsSummaryLimit})
        if isinstance(items, list):
            artifacts = items
    except Exception:
        pass
    data = {
        "task_id": task_id,
        "status": (row or {}).get("status", ""),
        "current_phase": (row or {}).get("current_phase", ""),
        "target": (row or {}).get("target", ""),
        "context": context,
        "artifacts_summary": artifacts,
        "generated_at": _now_iso(),
    }
    # ── FP 摘要：从 orchestrator 获取误报判定概况 ──
    try:
        fp = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}/fp-findings", timeout=3.0)
        data["fp_summary"] = {
            "total": fp.get("total", 0),
            "unverified": fp.get("unverified", 0),
            "suspicious": fp.get("suspicious", 0),
            "false_positives": fp.get("falsePositives", 0),
            "true_positives": fp.get("truePositives", 0),
            "inconclusive": fp.get("inconclusive", 0),
            "false_positive_rate": fp.get("falsePositiveRate", 0.0),
        }
    except Exception:
        pass
    return ok(data)

@app.get("/api/v1/tasks/{task_id}/todos")
async def task_todos(task_id: str) -> dict[str, Any]:
    try:
        items = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}/todos", timeout=8.0)
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    return ok({"taskId": task_id, "todos": items})


@app.get("/api/v1/tasks/{task_id}/full", response_model=None)
async def task_full(task_id: str, events_limit: int = 100) -> dict[str, Any] | JSONResponse:
    row = _get_task_row(task_id)
    if not row:
        return fail("任务不存在", code="NOT_FOUND", data={"taskId": task_id})
    events_resp = await task_events(task_id, events_limit)
    obs_resp = await task_observation(task_id)
    return ok(
        {
            "task": _task_row_to_api(row),
            "events": events_resp["data"],
            "observation": obs_resp["data"],
            "generatedAt": _now_iso(),
        }
    )


# ── 误报追踪 API ────────────────────────────────────────

@app.get("/api/v1/tasks/{task_id}/fp-findings", response_model=None)
async def task_fp_findings(task_id: str) -> dict[str, Any] | JSONResponse:
    """获取任务的 FP 判定汇总，代理到 orchestrator。"""
    try:
        data = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}/fp-findings", timeout=5.0)
    except Exception:
        data = {
            "taskId": task_id, "total": 0, "unverified": 0,
            "falsePositives": 0, "truePositives": 0, "inconclusive": 0,
            "falsePositiveRate": 0.0, "findings": [],
        }
    return ok(data)


@app.post("/api/v1/tasks/{task_id}/fp-feedback", response_model=None)
async def task_fp_feedback(
    task_id: str,
    fpId: str = Body(...),
    humanVerdict: Literal["FALSE_POSITIVE", "TRUE_POSITIVE", "INCONCLUSIVE"] = Body(...),
    feedback: str | None = Body(None),
    _user: CurrentUser = Depends(require_roles("ADMIN", "OPERATOR")),
) -> dict[str, Any] | JSONResponse:
    """提交人工 FP 判定反馈，代理到 orchestrator。"""
    try:
        result = await _orch("POST", f"/v1/orchestrator/tasks/{task_id}/fp-feedback",
                            json_body={"fpId": fpId, "humanVerdict": humanVerdict, "feedback": feedback},
                            timeout=5.0)
    except Exception as exc:
        return fail(f"反馈提交失败: {exc}", code="ORCHESTRATOR_ERROR")
    return ok(result)


@app.post("/api/v1/tasks/{task_id}/fp-findings/deep-audit", response_model=None)
async def task_fp_deep_audit(
    task_id: str,
    _user: CurrentUser = Depends(require_roles("ADMIN", "OPERATOR")),
) -> dict[str, Any] | JSONResponse:
    """触发 T2 深度离线审计，代理到 orchestrator。"""
    try:
        result = await _orch("POST", f"/v1/orchestrator/tasks/{task_id}/fp-findings:deep-audit", timeout=60.0)
    except Exception as exc:
        return fail(f"深度审计失败: {exc}", code="ORCHESTRATOR_ERROR")
    return ok(result)


def _read_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload", {})
    data = _json_loads(payload, default={})
    return data if isinstance(data, dict) else {}


def _strings_from(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return list(OrderedDict.fromkeys(out))


def _severity(text: str | None) -> str:
    s = (text or "").lower()
    if any(x in s for x in ("critical", "严重", "rce", "remote code", "9.")):
        return "critical"
    if any(x in s for x in ("high", "高危", "sql injection", "auth bypass", "unauthorized")):
        return "high"
    if any(x in s for x in ("medium", "中危", "xss", "ssrf", "file read")):
        return "medium"
    if any(x in s for x in ("low", "低危")):
        return "low"
    return "info"


def _cve(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"CVE-\d{4}-\d{4,7}", text, flags=re.I)
    return m.group(0).upper() if m else None


def _finding_key(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def _collect_findings(observation: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def add(title: Any, severity: str | None = None, evidence: str | None = None, phase: str | None = None, skill: str | None = None, cve: str | None = None) -> None:
        if title is None:
            return
        t = str(title).strip()
        if not t:
            return
        key = _finding_key(t)
        findings.setdefault(
            key,
            {
                "title": t,
                "severity": (severity or _severity(t)).lower(),
                "cve": cve or _cve(t),
                "evidence": evidence,
                "phase": phase,
                "skill": skill,
            },
        )

    ctx = observation.get("context") if isinstance(observation, dict) else {}
    if isinstance(ctx, dict):
        for item in _strings_from(ctx.get("confirmed_vulnerabilities")):
            add(item, severity=_severity(item), phase="CONFIRMED")
        for item in _strings_from(ctx.get("vulnerable_services")):
            add(item, severity="medium", phase="VULN_SCAN")
        raw_vulns = ctx.get("vulnerabilities")
        if isinstance(raw_vulns, list):
            for vuln in raw_vulns:
                if isinstance(vuln, dict):
                    add(
                        vuln.get("title") or vuln.get("name") or vuln.get("template_id") or vuln.get("id"),
                        severity=vuln.get("severity"),
                        evidence=vuln.get("evidence") or vuln.get("matched_at") or vuln.get("url"),
                        phase=vuln.get("phase"),
                        skill=vuln.get("skill_id"),
                        cve=vuln.get("cve"),
                    )
                else:
                    add(vuln)

    def collect_vuln_list(raw: Any, phase: str | None, skill: str | None) -> None:
        if isinstance(raw, str):
            raw = _json_loads(raw, default=[])
        if not isinstance(raw, list):
            return
        for item in raw:
            if isinstance(item, dict):
                add(
                    item.get("template_id") or item.get("name") or item.get("title") or item.get("vulnerability") or item.get("id") or item.get("url"),
                    severity=item.get("severity"),
                    evidence=item.get("evidence") or item.get("matched_at") or item.get("matcher") or item.get("url"),
                    phase=phase,
                    skill=skill,
                    cve=item.get("cve"),
                )
            else:
                add(item, phase=phase, skill=skill)

    for ev in events:
        payload = _read_payload(ev)
        phase = payload.get("phase")
        skill = payload.get("skill_id") or ev.get("source_module")
        parsed = payload.get("parsed_artifacts") or _json_loads(payload.get("parsed_artifacts_preview"), default={})
        if isinstance(parsed, dict):
            collect_vuln_list(parsed.get("vulnerabilities"), phase, skill)
            llm_ready = parsed.get("llm_ready")
            if isinstance(llm_ready, dict):
                collect_vuln_list(llm_ready.get("vulnerabilities"), phase, skill)
        collect_vuln_list(payload.get("vulnerabilities"), phase, skill)
        etype = str(ev.get("event_type") or "").lower()
        if any(k in etype for k in ("vulnerability", "vuln", "exploit_success", "nuclei_match")):
            add(payload.get("title") or payload.get("name") or payload.get("message") or etype, payload.get("severity"), payload.get("evidence"), phase, skill, payload.get("cve"))

    return list(findings.values())


def _recommendations(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = [
        (("sql", "注入"), "对所有数据库访问使用参数化查询；增加输入校验与最小权限数据库账号。", "high"),
        (("xss", "cross-site", "跨站"), "对输出进行上下文相关编码，启用 CSP，并过滤危险 HTML。", "medium"),
        (("rce", "remote code", "命令执行", "代码执行"), "立即升级受影响组件；限制可执行入口；在边界设备上临时阻断利用特征。", "critical"),
        (("unauthorized", "auth bypass", "未授权", "认证绕过"), "对敏感接口强制鉴权，补充权限校验和审计日志。", "high"),
        (("file", "path traversal", "目录穿越", "任意文件"), "规范化文件路径并限制访问根目录，禁止用户输入直接参与路径拼接。", "high"),
        (("ssrf",), "限制服务端请求目标，使用 allowlist，屏蔽内网和云元数据地址。", "medium"),
    ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in findings:
        title = str(f.get("title") or "")
        low = title.lower()
        suggestion = "升级相关组件至最新稳定版本；参考 CVE 数据库获取官方补丁；修复前通过 WAF 或访问控制临时缓解。"
        severity = f.get("severity") or "medium"
        for keys, msg, sev in rules:
            if any(k in low for k in keys):
                suggestion = msg
                severity = sev
                break
        if title and title not in seen:
            seen.add(title)
            out.append({"finding": title, "suggestion": suggestion, "severity": severity})
    return out


def _phase_statuses(status: str, phase: str) -> list[dict[str, str]]:
    current = PHASE_ORDER.index(phase) if phase in PHASE_ORDER else 0
    done = status == "DONE"
    out: list[dict[str, str]] = []
    for i, ph in enumerate(PHASE_ORDER):
        if done or i < current:
            st = "DONE"
        elif i == current:
            st = status or "RUNNING"
        else:
            st = "PENDING"
        out.append({"phase": ph, "status": st, "notes": f"{ph} {'已完成' if st == 'DONE' else '当前阶段' if i == current else '待执行'}"})
    return out


async def _executions(task_id: str, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    try:
        data = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}/executions", params={"limit": limit, "offset": offset}, timeout=10.0)
        return data if isinstance(data, dict) else {"executions": []}
    except Exception:
        return {"task_id": task_id, "executions": [], "error": "orchestrator unavailable"}


@app.get("/api/v1/tasks/{task_id}/report", response_model=None)
async def task_report(task_id: str) -> dict[str, Any] | JSONResponse:
    row = _get_task_row(task_id)
    if not row:
        return fail("任务不存在", code="NOT_FOUND", data={"taskId": task_id})
    obs = (await task_observation(task_id))["data"]
    events = (await task_events(task_id, 500))["data"]
    executions = await _executions(task_id, 100, 0)
    findings = _collect_findings(obs, events)
    hist = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for item in findings:
        sev = str(item.get("severity") or "info").lower()
        hist[sev if sev in hist else "info"] += 1
    risk = next((sev for sev in ("critical", "high", "medium", "low") if hist[sev] > 0), "none")
    ctx = obs.get("context", {})
    artifacts = [
        {"skillId": item.get("skill_id") or item.get("skillId") or "unknown", "summary": item.get("summary") or ""}
        for item in obs.get("artifacts_summary", [])
        if isinstance(item, dict)
    ]
    exec_items = executions.get("executions") or executions.get("items") or []

    # ── FP 数据注入：为每个 finding 标记误报判定状态 ──
    fp_data = None
    try:
        fp_raw = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}/fp-findings", timeout=3.0)
        if isinstance(fp_raw, dict) and fp_raw.get("findings"):
            fp_data = fp_raw
    except Exception:
        pass

    # 为 findings 注入 fpVerdict（三级 fallback 匹配）
    if fp_data:
        fp_map: dict[str, str] = {}  # url → verdict
        fp_records = fp_data.get("findings", [])
        for fpr in fp_records:
            url = (fpr.get("url") or "").strip()
            verdict = (fpr.get("currentVerdict") or "").strip()
            if url and verdict:
                fp_map[url.lower()] = verdict
        for finding in findings:
            # Level 1: 字符串 evidence 精确匹配
            raw = finding.get("evidence") or finding.get("url") or ""
            if isinstance(raw, list):
                raw = raw[0] if raw else ""
            evidence = str(raw).strip().lower()
            matched_verdict = fp_map.get(evidence)

            # Level 2: URL 子串 + title 匹配
            if not matched_verdict:
                title = (finding.get("title") or "").lower()
                for fp_url, fp_verdict in fp_map.items():
                    if fp_url in evidence or evidence in fp_url or fp_url in title:
                        matched_verdict = fp_verdict
                        break

            # Level 3: template_id 模糊匹配
            if not matched_verdict:
                title = (finding.get("title") or "").lower()
                for fpr in fp_records:
                    tid = (fpr.get("templateId") or "").lower()
                    if tid and len(tid) > 10 and tid in title:
                        matched_verdict = fpr.get("currentVerdict", "")
                        break

            finding["fpVerdict"] = matched_verdict or None

    fp_summary = None
    if fp_data:
        from collections import Counter
        vc = Counter(f.get("fpVerdict") for f in findings)
        fp_c = vc.get("FALSE_POSITIVE", 0)
        tp_c = vc.get("TRUE_POSITIVE", 0)
        r = fp_c + tp_c
        fp_summary = {
            "totalFindings": len(findings),
            "unverified": vc.get("UNVERIFIED", 0),
            "suspicious": vc.get("SUSPICIOUS", 0),
            "falsePositives": fp_c,
            "truePositives": tp_c,
            "inconclusive": vc.get("INCONCLUSIVE", 0),
            "falsePositiveRate": round(fp_c / r, 4) if r > 0 else 0.0,
        }

    report = {
        "taskId": task_id,
        "target": row.get("target") or "",
        "status": row.get("status") or "",
        "phases": _phase_statuses(row.get("status") or "PENDING", row.get("current_phase") or "RECON"),
        "summary": _build_summary(row, findings, exec_items, risk),
        "createdAt": _dt_iso(row.get("created_at")),
        "findings": findings,
        "recommendations": _recommendations(findings),
        "artifacts": artifacts,
        "openPorts": ctx.get("open_ports", []) if isinstance(ctx, dict) else [],
        "services": ctx.get("vulnerable_services", []) if isinstance(ctx, dict) else [],
        "severityHistogram": hist,
        "riskLevel": risk,
        "fpSummary": fp_summary,
        "executions": [
            {
                "phase": e.get("phase"),
                "skillId": e.get("skill_id"),
                "status": e.get("status"),
                "durationMs": e.get("duration_ms"),
                "createdAt": e.get("created_at"),
            }
            for e in exec_items
            if isinstance(e, dict)
        ],
    }
    return ok(report)


def _build_summary(row: dict[str, Any], findings: list[dict[str, Any]], executions: list[Any], risk: str) -> str:
    target = row.get("target") or "未知目标"
    desc = f"，{row.get('description')}" if row.get("description") else ""
    if row.get("status") == "DONE":
        if findings:
            return f"自动化渗透测试已完成，目标：{target}{desc}。共识别 {len(findings)} 项安全发现，整体风险等级 {risk}，执行 {len(executions)} 次技能调用。"
        return f"自动化渗透测试已完成，目标：{target}{desc}。未识别到明确可确认漏洞，建议保留本次证据并进行人工复核。"
    return f"自动化渗透测试正在执行，当前阶段：{row.get('current_phase') or 'RECON'}。目标：{target}{desc}。"


@app.get("/api/v1/tasks/{task_id}/trace")
async def trace(task_id: str, request: Request, executions_limit: int = 50, executions_offset: int = 0) -> dict[str, Any]:
    headers = _trace_headers(request)
    try:
        data = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}/trace", params={"executions_limit": executions_limit, "executions_offset": executions_offset}, headers=headers, timeout=15.0)
    except Exception:
        data = {"task_id": task_id, "plan": {}, "compile": {}, "executions": [], "error": "orchestrator unavailable"}
    return ok(data)


@app.get("/api/v1/tasks/{task_id}/trace/plan")
async def trace_plan(task_id: str, request: Request) -> dict[str, Any]:
    try:
        data = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}/trace/plan", headers=_trace_headers(request), timeout=15.0)
    except Exception:
        data = {"task_id": task_id, "plan": {}, "error": "orchestrator unavailable"}
    return ok(data)


@app.get("/api/v1/tasks/{task_id}/trace/compile")
async def trace_compile(task_id: str, request: Request) -> dict[str, Any]:
    try:
        data = await _orch("GET", f"/v1/orchestrator/tasks/{task_id}/trace/compile", headers=_trace_headers(request), timeout=15.0)
    except Exception:
        data = {"task_id": task_id, "compile": {}, "error": "orchestrator unavailable"}
    return ok(data)


@app.get("/api/v1/tasks/{task_id}/executions")
async def task_executions(task_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    return ok(await _executions(task_id, limit, offset))


@app.get("/api/v1/executions/{request_id}")
async def execution_record(request_id: str) -> dict[str, Any]:
    try:
        data = await _orch("GET", f"/v1/orchestrator/executions/{request_id}", timeout=10.0)
    except Exception:
        data = {"request_id": request_id, "status": "UNKNOWN", "error": "orchestrator unavailable"}
    return ok(data)


def _trace_headers(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("authorization", "x-orch-trace-token"):
        value = request.headers.get(key)
        if value:
            out[key] = value
    token = os.getenv("ORCH_TRACE_API_TOKEN")
    if token and "x-orch-trace-token" not in out and "authorization" not in out:
        out["x-orch-trace-token"] = token
    return out


@app.post("/api/v1/auth/login", response_model=None)
def login(req: LoginRequest) -> dict[str, Any] | JSONResponse:
    row = get_user_by_username(req.username.strip())
    if not row or row.get("status") != "ACTIVE" or not verify_password(row, req.password):
        _record_audit("LOGIN_FAILED", req.username, "", "登录失败")
        return fail("用户名或密码错误", code="UNAUTHORIZED")
    _execute("UPDATE tg_user SET last_login_at = NOW(), updated_at = NOW() WHERE user_id = %s", (row["user_id"],))
    row = get_user_by_username(req.username.strip()) or row
    _record_audit("LOGIN_SUCCESS", row["username"], row["user_id"], f"角色: {row.get('role')}")
    return ok({"token": issue_token(row), "user": _user_to_api(row), "expiresIn": AUTH_TOKEN_TTL_SECONDS})


@app.post("/api/v1/auth/register", response_model=None)
def register(req: RegisterRequest) -> dict[str, Any] | JSONResponse:
    if len(req.password) < 6:
        return fail("密码长度至少为6位", code="BAD_REQUEST")
    if get_user_by_username(req.username.strip()):
        return fail("用户名已存在", code="BAD_REQUEST")
    user_id = "user-" + uuid.uuid4().hex[:12]
    hashed = hash_password(req.password)
    _execute(
        """
        INSERT INTO tg_user (user_id, username, display_name, email, role, status, password_hash, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'VIEWER', 'ACTIVE', %s, NOW(), NOW())
        """,
        (user_id, req.username.strip(), req.displayName or req.username.strip(), req.email, hashed),
    )
    row = get_user_by_id(user_id)
    _record_audit("REGISTER", req.username, user_id, "角色: VIEWER")
    return ok({"token": issue_token(row or {}), "user": _user_to_api(row or {}), "expiresIn": AUTH_TOKEN_TTL_SECONDS})


@app.post("/api/v1/auth/logout")
def logout() -> dict[str, Any]:
    return ok({"loggedOut": True})


@app.get("/api/v1/auth/me", response_model=None)
def me(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any] | JSONResponse:
    row = get_user_by_id(user.user_id)
    if not row:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    return ok(_user_to_api(row))


@app.put("/api/v1/auth/me", response_model=None)
async def update_me(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any] | JSONResponse:
    row = get_user_by_id(user.user_id)
    if not row:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    body = await request.json()
    _execute(
        "UPDATE tg_user SET display_name = COALESCE(%s, display_name), email = COALESCE(%s, email), updated_at = NOW() WHERE user_id = %s",
        (body.get("displayName"), body.get("email"), row["user_id"]),
    )
    return ok(_user_to_api(get_user_by_id(row["user_id"]) or row))


@app.put("/api/v1/auth/me/password", response_model=None)
async def change_my_password(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any] | JSONResponse:
    row = get_user_by_id(user.user_id)
    body = await request.json()
    old_password = body.get("oldPassword") or ""
    new_password = body.get("newPassword") or ""
    if not row or not verify_password(row, old_password):
        return fail("当前密码不正确", code="BAD_REQUEST")
    if len(new_password) < 6:
        return fail("新密码长度至少为 6 位", code="BAD_REQUEST")
    _execute("UPDATE tg_user SET password_hash = %s, updated_at = NOW() WHERE user_id = %s", (hash_password(new_password), row["user_id"]))
    return ok({"updated": True, "userId": row["user_id"]})


@app.get("/api/v1/admin/users")
def list_users() -> dict[str, Any]:
    rows = _query("SELECT * FROM tg_user ORDER BY created_at DESC LIMIT 500")
    return ok([_user_to_api(r) for r in rows])


@app.get("/api/v1/admin/users/{user_id}", response_model=None)
def get_user(user_id: str) -> dict[str, Any] | JSONResponse:
    row = get_user_by_id(user_id)
    if not row:
        return fail("用户不存在", code="NOT_FOUND")
    return ok(_user_to_api(row))


@app.post("/api/v1/admin/users", response_model=None)
def create_user(req: UserCreateRequest) -> dict[str, Any] | JSONResponse:
    username = req.username.strip()
    if get_user_by_username(username):
        return fail("用户名已存在", code="BAD_REQUEST")
    role = (req.role or "VIEWER").upper()
    if role not in ("ADMIN", "OPERATOR", "VIEWER"):
        role = "VIEWER"
    user_id = "user-" + uuid.uuid4().hex[:12]
    hashed = hash_password(req.password or f"{username}123")
    _execute(
        """
        INSERT INTO tg_user (user_id, username, display_name, email, role, status, password_hash, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, 'ACTIVE', %s, NOW(), NOW())
        """,
        (user_id, username, req.displayName or username, req.email, role, hashed),
    )
    row = get_user_by_id(user_id)
    _record_audit("USER_CREATED", "admin", user_id, f"username={username} role={role}")
    return ok(_user_to_api(row or {}))


@app.put("/api/v1/admin/users/{user_id}", response_model=None)
async def update_user(user_id: str, request: Request) -> dict[str, Any] | JSONResponse:
    row = get_user_by_id(user_id)
    if not row:
        return fail("用户不存在", code="NOT_FOUND")
    body = await request.json()
    role = body.get("role")
    status = body.get("status")
    if role and role not in ("ADMIN", "OPERATOR", "VIEWER"):
        role = None
    if status and status not in ("ACTIVE", "DISABLED"):
        status = None
    _execute(
        """
        UPDATE tg_user
        SET display_name = COALESCE(%s, display_name),
            email = COALESCE(%s, email),
            role = COALESCE(%s, role),
            status = COALESCE(%s, status),
            updated_at = NOW()
        WHERE user_id = %s
        """,
        (body.get("displayName"), body.get("email"), role, status, user_id),
    )
    _record_audit("USER_UPDATED", "admin", user_id, "")
    return ok(_user_to_api(get_user_by_id(user_id) or row))


@app.delete("/api/v1/admin/users/{user_id}", response_model=None)
def delete_user(user_id: str) -> dict[str, Any] | JSONResponse:
    rows = _execute("DELETE FROM tg_user WHERE user_id = %s", (user_id,))
    if rows <= 0:
        return fail("用户不存在", code="NOT_FOUND")
    _record_audit("USER_DELETED", "admin", user_id, "")
    return ok({"deleted": True, "userId": user_id})


@app.put("/api/v1/admin/users/{user_id}/password", response_model=None)
async def set_user_password(user_id: str, request: Request) -> dict[str, Any] | JSONResponse:
    row = get_user_by_id(user_id)
    if not row:
        return fail("用户不存在", code="NOT_FOUND")
    body = await request.json()
    password = body.get("password") or body.get("newPassword") or ""
    if len(password) < 6:
        return fail("密码长度至少为 6 位", code="BAD_REQUEST")
    _execute("UPDATE tg_user SET password_hash = %s, updated_at = NOW() WHERE user_id = %s", (hash_password(password), user_id))
    return ok({"updated": True, "userId": user_id})


def _task_stats() -> dict[str, int]:
    stats = {k.lower(): 0 for k in STATUS_VALUES}
    rows = _query("SELECT status, COUNT(*) AS cnt FROM tg_task GROUP BY status")
    total = 0
    for row in rows:
        status = str(row.get("status") or "").lower()
        cnt = int(row.get("cnt") or 0)
        total += cnt
        if status:
            stats[status] = cnt
    stats["total"] = total
    return stats


@app.get("/api/v1/admin/tasks/stats")
def task_stats() -> dict[str, Any]:
    return ok(_task_stats())


@app.post("/api/v1/admin/tasks/bulk-stop")
async def bulk_stop() -> dict[str, Any]:
    rows = _query("SELECT task_id FROM tg_task WHERE status = 'RUNNING' LIMIT 200")
    stopped = 0
    for row in rows:
        try:
            await _orch("POST", f"/v1/orchestrator/tasks/{row['task_id']}/stop", timeout=8.0)
            stopped += 1
        except Exception:
            pass
    _execute("UPDATE tg_task SET status = 'PAUSED', updated_at = NOW() WHERE status = 'RUNNING'")
    return ok({"stopped": stopped})


@app.delete("/api/v1/admin/tasks/completed")
def cleanup_completed() -> dict[str, Any]:
    rows = _execute("DELETE FROM tg_task WHERE status IN ('DONE','FAILED','CANCELLED')", ())
    return ok({"deleted": rows})


@app.get("/api/v1/admin/reports")
def admin_reports(limit: int = 100) -> dict[str, Any]:
    rows = _query(
        "SELECT * FROM tg_task WHERE status = 'DONE' ORDER BY updated_at DESC LIMIT %s",
        (_limit(limit, 100, 200),),
    )
    return ok([_task_row_to_api(r) for r in rows])


@app.get("/api/v1/admin/events/recent")
def recent_events(limit: int = 30) -> dict[str, Any]:
    rows = _query(
        """
        SELECT task_id, event_type, ts, source_module
        FROM tg_trace_events
        ORDER BY id DESC
        LIMIT %s
        """,
        (_limit(limit, 30, 200),),
    )
    return ok(
        [
            {
                "taskId": r.get("task_id"),
                "eventType": r.get("event_type"),
                "ts": _dt_iso(r.get("ts")),
                "sourceModule": r.get("source_module"),
            }
            for r in rows
        ]
    )


@app.get("/api/v1/admin/dashboard/summary")
def dashboard_summary() -> dict[str, Any]:
    stats = _task_stats()
    active = _query("SELECT * FROM tg_task WHERE status IN ('RUNNING','PAUSED') ORDER BY updated_at DESC LIMIT 10")
    completed = _query("SELECT * FROM tg_task WHERE status = 'DONE' ORDER BY updated_at DESC LIMIT 5")
    recent = recent_events(20)["data"]
    return ok(
        {
            "task_stats": stats,
            "recent_events": recent,
            "active_tasks": [
                {"taskId": r["task_id"], "name": r.get("name"), "target": r.get("target"), "status": r.get("status"), "currentPhase": r.get("current_phase"), "updatedAt": _dt_iso(r.get("updated_at"))}
                for r in active
            ],
            "recent_completed": [
                {"taskId": r["task_id"], "name": r.get("name"), "target": r.get("target"), "updatedAt": _dt_iso(r.get("updated_at"))}
                for r in completed
            ],
            "generated_at": _now_iso(),
        }
    )


@app.get("/api/v1/admin/monitor/snapshot")
async def monitor_snapshot(event_limit: int = 30) -> dict[str, Any]:
    stats = _task_stats()
    active = _query("SELECT * FROM tg_task WHERE status IN ('RUNNING','PAUSED') ORDER BY updated_at DESC LIMIT 20")
    recent_tasks = _query("SELECT * FROM tg_task ORDER BY updated_at DESC LIMIT 20")
    mq = await _proxy_or_default("/v1/orchestrator/mq-status", {"mode": "http", "queue": "execute_tasks_agent", "messages_ready": 0, "consumers": 0})
    return ok(
        {
            "taskStats": stats,
            "activeTasks": [_task_row_to_api(r) for r in active],
            "recentTasks": [{"taskId": r["task_id"], "name": r.get("name"), "status": r.get("status"), "currentPhase": r.get("current_phase"), "updatedAt": _dt_iso(r.get("updated_at"))} for r in recent_tasks],
            "recentEvents": recent_events(event_limit)["data"],
            "mqStatus": mq,
            "snapshotAt": _now_iso(),
        }
    )


async def _proxy_or_default(path: str, default: Any, params: dict[str, Any] | None = None, method: str = "GET", body: Any = None) -> Any:
    try:
        return await _orch(method, path, params=params, json_body=body, timeout=15.0)
    except Exception:
        return default


def _service_port(base_url: str, fallback: int) -> int:
    parsed = urlparse(base_url)
    return int(parsed.port or fallback)


async def _http_health_status(name: str, role: str, base_url: str, fallback_port: int) -> dict[str, Any]:
    checked_at = _now_iso()
    port = _service_port(base_url, fallback_port)
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(f"{base_url}/health")
        detail = ""
        try:
            body = resp.json()
            detail = str(body.get("status") or body.get("service") or "")
        except Exception:
            detail = resp.reason_phrase
        if resp.is_success:
            return {"role": role, "port": port, "status": "UP", "detail": detail or "ok", "checkedAt": checked_at}
        return {"role": role, "port": port, "status": "DOWN", "detail": f"HTTP {resp.status_code}", "checkedAt": checked_at}
    except Exception as exc:
        return {"role": role, "port": port, "status": "DOWN", "detail": exc.__class__.__name__, "checkedAt": checked_at}


async def _tcp_health_status(role: str, host: str, port: int) -> dict[str, Any]:
    checked_at = _now_iso()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2.0)
        writer.close()
        await writer.wait_closed()
        return {"role": role, "port": port, "status": "UP", "detail": f"{host}:{port} reachable", "checkedAt": checked_at}
    except Exception as exc:
        return {"role": role, "port": port, "status": "DOWN", "detail": exc.__class__.__name__, "checkedAt": checked_at}


def _mq_endpoint() -> tuple[str, int]:
    parsed = urlparse(MQ_BROKER_URL)
    return parsed.hostname or "localhost", int(parsed.port or 5672)


def _qdrant_endpoint() -> tuple[str, int]:
    parsed = urlparse(KB_QDRANT_URL)
    return parsed.hostname or "localhost", int(parsed.port or 6333)


async def _service_health_map() -> dict[str, dict[str, Any]]:
    mq_host, mq_port = _mq_endpoint()
    qdrant_host, qdrant_port = _qdrant_endpoint()
    orchestrator, executor, evidence, mq, qdrant = await asyncio.gather(
        _http_health_status("orchestrator", "LLM State Machine", ORCHESTRATOR_BASE_URL, 18081),
        _http_health_status("executor", "Skill Runner", EXECUTOR_BASE_URL, 18102),
        _http_health_status("evidence", "Observability Store", EVIDENCE_BASE_URL, 18103),
        _tcp_health_status("Async Task Queue", mq_host, mq_port),
        _tcp_health_status("Vector KB", qdrant_host, qdrant_port),
    )
    return {
        "gateway": {"role": "REST API Gateway", "port": 18080, "status": "UP", "detail": "ok", "checkedAt": _now_iso()},
        "orchestrator": orchestrator,
        "executor": executor,
        "evidence": evidence,
        "mq": mq,
        "qdrant": qdrant,
    }


@app.get("/api/v1/admin/mq-status")
async def mq_status() -> dict[str, Any]:
    return ok(await _proxy_or_default("/v1/orchestrator/mq-status", {"mode": "http", "queue": "execute_tasks_agent", "messages_ready": 0, "consumers": 0}))


@app.get("/api/v1/admin/orchestrator/sli/snapshot")
async def sli_snapshot(include_mq: bool = True) -> dict[str, Any]:
    return ok(await _proxy_or_default("/v1/orchestrator/sli/snapshot", {"total_ticks": 0, "failed_ticks": 0, "active_tasks": _task_stats().get("running", 0)}, {"include_mq": include_mq}))


@app.get("/api/v1/admin/v1/overview")
async def v1_overview() -> dict[str, Any]:
    default = {"v1_scheduling": {"active_tasks": _task_stats().get("running", 0)}, "v1_mq_lanes": {}, "v1_kb": {}, "v1_agent_registry": {"total": 0}}
    return ok(await _proxy_or_default("/v1/orchestrator/admin/v1/overview", default))


@app.get("/api/v1/admin/v1/health-overview")
async def v1_health_overview() -> dict[str, Any]:
    return ok(await _proxy_or_default("/v1/orchestrator/admin/v1/health-overview", {"health": {"status": "degraded", "message": "orchestrator unavailable"}}))


@app.get("/api/v1/admin/v1/scheduling-observe")
async def scheduling_observe(phase: str | None = None, task_id: str | None = None, preferred_capability: str | None = None) -> dict[str, Any]:
    return ok(await _proxy_or_default("/v1/orchestrator/admin/v1/scheduling-observe", {"scheduling": {"active_tasks": _task_stats().get("running", 0)}}, {"phase": phase, "task_id": task_id, "preferred_capability": preferred_capability}))


@app.get("/api/v1/admin/v1/kb-observe")
async def kb_observe() -> dict[str, Any]:
    return ok(await _proxy_or_default("/v1/orchestrator/admin/v1/kb-observe", {"enabled": False, "kb_backend": "qdrant", "total_chunks": 0}))


@app.get("/api/v1/admin/v1/kb-federation-observe")
async def kb_federation() -> dict[str, Any]:
    return ok(await _proxy_or_default("/v1/orchestrator/admin/v1/kb-federation-observe", {"enabled": False, "stores": []}))


@app.get("/api/v1/admin/skills")
async def skills(phase: str | None = None) -> dict[str, Any]:
    data = await _proxy_or_default("/v1/orchestrator/admin/skills", {"skill_ids": [], "skills": [], "error": "orchestrator unavailable"}, {"phase": phase})
    return ok(data)


@app.get("/api/v1/admin/audit/events")
def audit_events(limit: int = 50) -> dict[str, Any]:
    rows = _query(
        """
        SELECT event_type, ts, task_id, payload
        FROM tg_trace_events
        WHERE source_module = 'gateway'
        ORDER BY id DESC
        LIMIT %s
        """,
        (_limit(limit, 50, 200),),
    )
    events = []
    for r in rows:
        payload = _json_loads(r.get("payload"))
        events.append(
            {
                "type": r.get("event_type") or "",
                "actor": payload.get("actor", "system") if isinstance(payload, dict) else "system",
                "target": payload.get("target", r.get("task_id")) if isinstance(payload, dict) else r.get("task_id"),
                "detail": payload.get("detail", "") if isinstance(payload, dict) else "",
                "timestamp": r.get("ts") or _now_iso(),
            }
        )
    return ok(events)


@app.get("/api/v1/admin/audit/summary")
def audit_summary() -> dict[str, Any]:
    events = audit_events(200)["data"]
    by_type = Counter(e["type"] for e in events)
    return ok({"total": len(events), "by_type": dict(by_type), "login_failures": by_type.get("LOGIN_FAILED", 0), "generated_at": _now_iso()})


@app.get("/api/v1/admin/analytics/overview")
def analytics() -> dict[str, Any]:
    stats = _task_stats()
    rows = _query("SELECT event_type, COUNT(*) AS cnt FROM tg_trace_events GROUP BY event_type ORDER BY cnt DESC LIMIT 50")
    breakdown = {str(r.get("event_type")): int(r.get("cnt") or 0) for r in rows}
    total = stats.get("total", 0)
    return ok(
        {
            "task_stats": stats,
            "completion_rate": (stats.get("done", 0) / total) if total else 0.0,
            "recent_events_count": sum(breakdown.values()),
            "event_type_breakdown": breakdown,
            "skill_execution_breakdown": {},
            "total_executions": 0,
            "total_plans": 0,
            "generated_at": _now_iso(),
        }
    )


@app.post("/api/v1/admin/tasks/batch")
async def batch_create(request: Request) -> dict[str, Any]:
    body = await request.json()
    targets = body.get("targets") if isinstance(body, dict) else []
    if not isinstance(targets, list):
        targets = []
    targets = [str(t).strip() for t in targets if str(t).strip()][:20]
    auto_start = bool(body.get("auto_start")) if isinstance(body, dict) else False
    prefix = str(body.get("name_prefix") or "批量任务") if isinstance(body, dict) else "批量任务"
    desc = body.get("description") if isinstance(body, dict) else None
    results = []
    started = 0
    for idx, target in enumerate(targets, 1):
        created = await create_task(CreateTaskRequest(name=f"{prefix}-{idx}", target=target, description=desc))
        task = created["data"]
        results.append({"taskId": task["taskId"], "name": task["name"], "target": target})
        if auto_start:
            run_result = await _run_lifecycle(task["taskId"], "run", max_ticks=100)
            if isinstance(run_result, dict) and run_result.get("code") == "0":
                started += 1
    return ok({"created": len(results), "auto_started": auto_start, "started_count": started, "tasks": results, "generated_at": _now_iso()})


@app.get("/api/v1/admin/vulns/summary")
async def vulns_summary(task_limit: int = 20) -> dict[str, Any]:
    rows = _query("SELECT * FROM tg_task ORDER BY updated_at DESC LIMIT %s", (_limit(task_limit, 20, 100),))
    by_task = []
    total = 0
    for row in rows:
        obs = (await task_observation(row["task_id"]))["data"]
        events = (await task_events(row["task_id"], 300))["data"]
        vulns = _collect_findings(obs, events)
        total += len(vulns)
        by_task.append({"task_id": row["task_id"], "task_name": row.get("name"), "target": row.get("target"), "vuln_count": len(vulns), "vulnerabilities": vulns})
    return ok({"tasks_analyzed": len(rows), "total_vulns": total, "by_task": by_task, "generated_at": _now_iso()})


@app.get("/api/v1/admin/config/runtime")
def runtime_config() -> dict[str, Any]:
    return ok(
        {
            "llm": {
                "provider": os.getenv("LLM_PROVIDER", "openai_compat"),
                "model": os.getenv("LLM_MODEL_ID") or os.getenv("OPENAI_MODEL_ID") or os.getenv("ANTHROPIC_MODEL_ID") or "",
                "endpoint_host": os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL") or "",
                "api_key_set": bool(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")),
            },
            "execution": {
                "dispatch_mode": os.getenv("EXECUTION_DISPATCH_MODE", "http"),
                "max_concurrent": os.getenv("MAX_CONCURRENT_TASKS_RUNNING", "0"),
                "plan_mode": os.getenv("ORCH_PLAN_MODE", "default"),
                "task_store": os.getenv("ORCH_TASK_STORE_BACKEND", "redis"),
            },
            "features": {
                "kb_enabled": os.getenv("KB_ENABLED", "true"),
                "manager_agent": os.getenv("ENABLE_MANAGER_AGENT", "false"),
                "skill_containers": os.getenv("EXECUTOR_USE_SKILL_CONTAINERS", "true"),
            },
            "deployment": {"mode": os.getenv("DEPLOYMENT_MODE", "docker"), "workspace_root": os.getenv("WORKSPACE_ROOT", "/data/workspace")},
            "generated_at": _now_iso(),
        }
    )


_CONFIG_OVERRIDES: dict[str, str] = {}


@app.get("/api/v1/admin/config/overrides")
def config_overrides() -> dict[str, Any]:
    return ok(_CONFIG_OVERRIDES)


@app.put("/api/v1/admin/config/override")
async def set_config_override(request: Request) -> dict[str, Any]:
    body = await request.json()
    if isinstance(body, dict):
        for k, v in body.items():
            _CONFIG_OVERRIDES[str(k)] = str(v)
    return ok({"applied": len(body) if isinstance(body, dict) else 0, "total_overrides": len(_CONFIG_OVERRIDES)})


@app.delete("/api/v1/admin/config/override")
def clear_config_override() -> dict[str, Any]:
    _CONFIG_OVERRIDES.clear()
    return ok({"cleared": True})


@app.delete("/api/v1/admin/config/override/{key}")
def delete_config_override(key: str) -> dict[str, Any]:
    _CONFIG_OVERRIDES.pop(key, None)
    return ok({"deleted": True, "key": key})




# ── Alert Triage API ─────────────────────────────────────────────────

ORCH_ALERT_TRIAGE_PATH = "/v1/orchestrator/alert-triage"


class CreateAlertTriageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    alert_uuid: str = Field(min_length=1, validation_alias=AliasChoices("alert_uuid", "alertUuid"))
    scenario_id: str | None = Field(default=None, validation_alias=AliasChoices("scenario_id", "scenarioId"))
    enable_rag: bool = Field(default=True, validation_alias=AliasChoices("enable_rag", "enableRag"))
    strategy_params: dict[str, Any] | None = Field(default=None, validation_alias=AliasChoices("strategy_params", "strategyParams"))
    caller_notes: str | None = Field(default=None, validation_alias=AliasChoices("caller_notes", "callerNotes"))


@app.post("/api/v1/alert-triage/tasks")
async def create_alert_triage_task(req: CreateAlertTriageRequest) -> dict[str, Any]:
    return ok(await _create_alert_triage_task_impl(req))


async def _create_alert_triage_task_impl(
    req: CreateAlertTriageRequest,
    *,
    task_id: str | None = None,
    auto_start: bool = True,
) -> dict[str, Any]:
    task_id = task_id or "at-" + uuid.uuid4().hex
    row = _get_task_row(task_id)
    if row is None:
        _execute(
            """
            INSERT INTO tg_task
              (task_id, name, target, status, current_phase, created_at, updated_at)
            VALUES (%s, %s, %s, 'PENDING', 'RECON', NOW(), NOW())
            """,
            (task_id, f"研判-{req.alert_uuid[:8]}", req.alert_uuid),
        )
        row = _get_task_row(task_id)
    payload = {
        "task_id": task_id,
        "alert_uuid": req.alert_uuid,
        "scenario_id": req.scenario_id,
        "enable_rag": req.enable_rag,
        "strategy_params": req.strategy_params,
        "caller_notes": req.caller_notes,
    }
    try:
        await _orch("POST", f"{ORCH_ALERT_TRIAGE_PATH}/tasks", json_body=payload, timeout=30.0)
    except Exception as exc:
        log.warning("alert triage create failed task_id=%s: %s", task_id, exc)
        _execute("UPDATE tg_task SET status = 'FAILED', updated_at = NOW() WHERE task_id = %s", (task_id,))
        raise HTTPException(status_code=502, detail="创建研判任务失败") from exc

    current_status = str((row or {}).get("status") or "PENDING").upper()
    if auto_start and current_status not in {"RUNNING", "DONE"}:
        # The graph may include XDR retries and an LLM request. Do not keep the
        # caller request open while it runs; the task is visible and pollable now.
        asyncio.create_task(_run_alert_triage_in_background(task_id))
    return _triage_task_to_api({
        "task_id": task_id,
        "alert_uuid": req.alert_uuid,
        "status": current_status,
        "enable_rag": req.enable_rag,
    }, row)

@app.get("/api/v1/alert-triage/tasks")
async def list_alert_triage_tasks(limit: int = 50) -> dict[str, Any]:
    rows = _query(
        "SELECT task_id, name, target, status, created_at, updated_at FROM tg_task WHERE task_id LIKE 'at-%%' ORDER BY created_at DESC LIMIT %s",
        (_limit(limit, 20, 200),),
    )
    tasks = [_triage_task_to_api(None, row) for row in rows]

    # Enrich completed tasks with orchestrator state (batch, best-effort)
    done_ids = [t["taskId"] for t in tasks if t["status"] in ("DONE", "FAILED")]
    if done_ids:
        async def _fetch_one(tid: str) -> tuple[str, dict[str, Any] | None]:
            return tid, await _get_alert_triage_state(tid)
        results = await asyncio.gather(*(_fetch_one(tid) for tid in done_ids[:20]))
        state_map = {tid: state for tid, state in results if state is not None}
        for index, task in enumerate(tasks):
            s = state_map.get(task["taskId"])
            if s:
                tasks[index] = _triage_task_to_api(s, rows[index])

    return ok(tasks)


@app.get("/api/v1/alert-triage/tasks/{task_id}")
async def get_alert_triage_task(task_id: str) -> dict[str, Any]:
    row = _get_task_row(task_id)
    if not row:
        return fail("任务不存在", code="NOT_FOUND", data={"taskId": task_id})
    state = await _get_alert_triage_state(task_id)
    return ok(_triage_task_to_api(state, row))


@app.post("/api/v1/alert-triage/tasks/{task_id}/run")
async def run_alert_triage_task(task_id: str) -> dict[str, Any]:
    row = _get_task_row(task_id)
    if not row:
        return fail("任务不存在", code="NOT_FOUND", data={"taskId": task_id})
    try:
        result = await _orch("POST", f"{ORCH_ALERT_TRIAGE_PATH}/tasks/{task_id}/run", timeout=120.0)
        _sync_task_state(task_id, result)
        return ok(_triage_task_to_api(result if isinstance(result, dict) else None, _get_task_row(task_id) or row))
    except Exception as exc:
        log.warning("alert triage run failed task_id=%s: %s", task_id, exc)
        return fail(f"研判执行失败: {exc}", code="ORCHESTRATOR_ERROR")


@app.get("/api/v1/alert-triage/tasks/{task_id}/events")
async def get_alert_triage_events(task_id: str, limit: int = 500) -> dict[str, Any]:
    return await task_events(task_id, limit)


@app.get("/api/v1/alert-triage/tasks/{task_id}/report")
async def get_alert_triage_report(task_id: str) -> dict[str, Any]:
    row = _get_task_row(task_id)
    if not row:
        return fail("任务不存在", code="NOT_FOUND", data={"taskId": task_id})
    try:
        result = await _orch("GET", f"{ORCH_ALERT_TRIAGE_PATH}/tasks/{task_id}", timeout=10.0)
    except Exception as exc:
        return fail(f"获取研判报告失败: {exc}", code="ORCHESTRATOR_ERROR")
    return ok(result)


@app.get("/api/v1/system/info")
async def system_info() -> dict[str, Any]:
    uptime = int((datetime.now(timezone.utc) - START_TIME).total_seconds())
    info = {
        "platform": "TrustGuard Agent",
        "version": "1.0.0",
        "edition": "Competition Edition",
        "apiVersion": "v1",
        "runtime": f"Python {sys.version.split()[0]}",
        "os": f"{platform.system()} {platform.machine()}",
        "startTime": START_TIME.isoformat().replace("+00:00", "Z"),
        "uptimeSeconds": uptime,
        "taskStats": _task_stats(),
        "services": await _service_health_map(),
        "capabilities": {
            "skillContainers": "docker-compose profile",
            "phases": len(PHASE_ORDER),
            "phaseList": PHASE_ORDER,
            "concurrentTargets": os.getenv("MAX_CONCURRENT_TASKS_RUNNING", "unlimited"),
            "dispatchModes": ["http", "mq"],
            "llmProviders": ["openai_compat", "anthropic", "gemini", "local"],
        },
    }
    return ok(info)
