from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import platform
import re
import sys
import uuid
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
import bcrypt
import pymysql
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pymysql.cursors import DictCursor


log = logging.getLogger("trustguard.gateway")

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "trustguard")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "trustguard")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "trustguard_agent")
ORCHESTRATOR_BASE_URL = os.getenv("ORCHESTRATOR_BASE_URL", "http://localhost:18081").rstrip("/")
EVIDENCE_BASE_URL = os.getenv("EVIDENCE_BASE_URL", "http://localhost:18103").rstrip("/")
EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://localhost:18102").rstrip("/")
MQ_BROKER_URL = os.getenv("MQ_BROKER_URL", "amqp://guest:guest@localhost:5672/")
KB_QDRANT_URL = os.getenv("KB_QDRANT_URL", "http://localhost:6333").rstrip("/")

START_TIME = datetime.now(timezone.utc)
PHASE_ORDER = ["RECON", "THREAT_MODEL", "VULN_SCAN", "EXPLOIT", "REPORT", "DONE"]
STATUS_VALUES = ["PENDING", "RUNNING", "PAUSED", "DONE", "FAILED", "CANCELLED"]

app = FastAPI(title="TrustGuard Gateway", version="1.0.0")


def ok(data: Any = None, message: str = "success") -> dict[str, Any]:
    return {"code": "0", "message": message, "data": data}


def fail(message: str, code: str = "BAD_REQUEST", data: Any = None, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": message, "data": data})


def _conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=DictCursor,
    )


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])


def _execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            return int(cur.execute(sql, params))


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


def _limit(value: int | None, default: int, max_value: int = 1000) -> int:
    if value is None or value <= 0:
        return default
    return min(value, max_value)


def _task_row_to_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "taskId": row.get("task_id") or "",
        "name": row.get("name") or "",
        "target": row.get("target") or "",
        "description": row.get("description") or "",
        "status": row.get("status") or "PENDING",
        "currentPhase": row.get("current_phase") or "RECON",
        "createdAt": _dt_iso(row.get("created_at")) or "",
        "updatedAt": _dt_iso(row.get("updated_at")) or "",
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
    if status or phase:
        parts: list[str] = []
        params: list[Any] = []
        if status:
            parts.append("status = %s")
            params.append(status)
        if phase:
            parts.append("current_phase = %s")
            params.append(phase)
        parts.append("updated_at = NOW()")
        params.append(task_id)
        _execute(f"UPDATE tg_task SET {', '.join(parts)} WHERE task_id = %s", tuple(params))


async def _orch(method: str, path: str, *, json_body: Any = None, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 30.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, f"{ORCHESTRATOR_BASE_URL}{path}", json=json_body, params=params, headers=headers)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()


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
    }
    state = await _orch("POST", f"/v1/orchestrator/tasks/{task_id}/restore", json_body=payload, timeout=20.0)
    _sync_task_state(task_id, state)
    return state


async def _best_effort_restore(row: dict[str, Any]) -> None:
    try:
        await _restore_task(row)
    except Exception as exc:
        log.warning("orchestrator restore failed task_id=%s: %s", row.get("task_id"), exc)


def _extract_username(auth_header: str | None) -> str | None:
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:].strip()
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=False).decode("utf-8", errors="ignore")
    except Exception:
        return None
    username = raw.split(":", 1)[0].strip()
    return username or None


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


def _get_user_by_username(username: str) -> dict[str, Any] | None:
    rows = _query("SELECT * FROM tg_user WHERE username = %s", (username,))
    return rows[0] if rows else None


def _get_user_by_id(user_id: str) -> dict[str, Any] | None:
    rows = _query("SELECT * FROM tg_user WHERE user_id = %s", (user_id,))
    return rows[0] if rows else None


def _verify_password(row: dict[str, Any], password: str) -> bool:
    hashed = row.get("password_hash")
    if isinstance(hashed, str) and hashed:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            log.warning("password hash verification failed for username=%s", row.get("username"))
    return False


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _token(username: str) -> str:
    raw = f"{username}:{uuid.uuid4().hex}"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _record_audit(event_type: str, actor: str, target: str = "", detail: str = "") -> None:
    # The current schema does not include an audit table. Recent audit endpoints derive from trace events.
    try:
        _execute(
            """
            INSERT INTO tg_trace_events
              (task_id, event_id, ts, event_type, source_module, payload, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                target or "platform",
                "evt-" + uuid.uuid4().hex[:12],
                _now_iso(),
                event_type,
                "gateway",
                json.dumps({"actor": actor, "target": target, "detail": detail}, ensure_ascii=False),
            ),
        )
    except Exception:
        log.debug("audit record skipped", exc_info=True)


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    target: str = Field(min_length=1)
    description: str | None = None
    business_background: str | None = Field(default=None, validation_alias=AliasChoices("business_background", "businessBackground"))
    extra_user_requirements: str | None = Field(default=None, validation_alias=AliasChoices("extra_user_requirements", "extraUserRequirements"))


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


@app.post("/api/v1/tasks")
async def create_task(req: CreateTaskRequest) -> dict[str, Any]:
    task_id = "task-" + uuid.uuid4().hex
    name = (req.name or "").strip() or "未命名任务"
    target = req.target.strip()
    _execute(
        """
        INSERT INTO tg_task
          (task_id, name, description, business_background, extra_user_requirements,
           target, status, current_phase, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', 'RECON', NOW(), NOW())
        """,
        (task_id, name, req.description, req.business_background, req.extra_user_requirements, target),
    )
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
            },
            timeout=10.0,
        )
    except Exception as exc:
        log.warning("orchestrator create failed task_id=%s: %s", task_id, exc)
    return ok(_task_row_to_api(row or {}))


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
        data = await _evidence("GET", f"/internal/tasks/{task_id}/events", params={"limit": _limit(limit, 500)})
    except Exception:
        rows = _query(
            """
            SELECT event_id, task_id, ts, event_type, source_module, payload
            FROM tg_trace_events
            WHERE task_id = %s
            ORDER BY ts ASC, id ASC
            LIMIT %s
            """,
            (task_id, _limit(limit, 500)),
        )
        data = [
            {
                "event_id": r.get("event_id"),
                "task_id": r.get("task_id"),
                "timestamp": _dt_iso(r.get("ts")),
                "event_type": r.get("event_type"),
                "source_module": r.get("source_module"),
                "payload": _json_loads(r.get("payload")),
            }
            for r in rows
        ]
    return ok(data or [])


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
    row = _get_user_by_username(req.username.strip())
    if not row or row.get("status") != "ACTIVE" or not _verify_password(row, req.password):
        _record_audit("LOGIN_FAILED", req.username, "", "登录失败")
        return fail("用户名或密码错误", code="UNAUTHORIZED")
    _execute("UPDATE tg_user SET last_login_at = NOW(), updated_at = NOW() WHERE user_id = %s", (row["user_id"],))
    row = _get_user_by_username(req.username.strip()) or row
    _record_audit("LOGIN_SUCCESS", row["username"], row["user_id"], f"角色: {row.get('role')}")
    return ok({"token": _token(row["username"]), "user": _user_to_api(row), "expiresIn": 86400})


@app.post("/api/v1/auth/register", response_model=None)
def register(req: RegisterRequest) -> dict[str, Any] | JSONResponse:
    if len(req.password) < 6:
        return fail("密码长度至少为6位", code="BAD_REQUEST")
    if _get_user_by_username(req.username.strip()):
        return fail("用户名已存在", code="BAD_REQUEST")
    user_id = "user-" + uuid.uuid4().hex[:12]
    hashed = _hash_password(req.password)
    _execute(
        """
        INSERT INTO tg_user (user_id, username, display_name, email, role, status, password_hash, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'VIEWER', 'ACTIVE', %s, NOW(), NOW())
        """,
        (user_id, req.username.strip(), req.displayName or req.username.strip(), req.email, hashed),
    )
    row = _get_user_by_id(user_id)
    _record_audit("REGISTER", req.username, user_id, "角色: VIEWER")
    return ok({"token": _token(req.username.strip()), "user": _user_to_api(row or {}), "expiresIn": 86400})


@app.post("/api/v1/auth/logout")
def logout() -> dict[str, Any]:
    return ok({"loggedOut": True})


@app.get("/api/v1/auth/me", response_model=None)
def me(authorization: str | None = Header(default=None)) -> dict[str, Any] | JSONResponse:
    username = _extract_username(authorization)
    if not username:
        return fail("缺少或无效的 Authorization 请求头", code="UNAUTHORIZED")
    row = _get_user_by_username(username)
    if not row or row.get("status") != "ACTIVE":
        return fail("用户不存在或已禁用", code="UNAUTHORIZED")
    return ok(_user_to_api(row))


@app.put("/api/v1/auth/me", response_model=None)
async def update_me(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any] | JSONResponse:
    username = _extract_username(authorization)
    if not username:
        return fail("未登录", code="UNAUTHORIZED")
    row = _get_user_by_username(username)
    if not row:
        return fail("用户不存在", code="UNAUTHORIZED")
    body = await request.json()
    _execute(
        "UPDATE tg_user SET display_name = COALESCE(%s, display_name), email = COALESCE(%s, email), updated_at = NOW() WHERE user_id = %s",
        (body.get("displayName"), body.get("email"), row["user_id"]),
    )
    return ok(_user_to_api(_get_user_by_id(row["user_id"]) or row))


@app.put("/api/v1/auth/me/password", response_model=None)
async def change_my_password(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any] | JSONResponse:
    username = _extract_username(authorization)
    if not username:
        return fail("未登录", code="UNAUTHORIZED")
    row = _get_user_by_username(username)
    body = await request.json()
    old_password = body.get("oldPassword") or ""
    new_password = body.get("newPassword") or ""
    if not row or not _verify_password(row, old_password):
        return fail("当前密码不正确", code="BAD_REQUEST")
    if len(new_password) < 6:
        return fail("新密码长度至少为 6 位", code="BAD_REQUEST")
    _execute("UPDATE tg_user SET password_hash = %s, updated_at = NOW() WHERE user_id = %s", (_hash_password(new_password), row["user_id"]))
    return ok({"updated": True, "userId": row["user_id"]})


@app.get("/api/v1/admin/users")
def list_users() -> dict[str, Any]:
    rows = _query("SELECT * FROM tg_user ORDER BY created_at DESC LIMIT 500")
    return ok([_user_to_api(r) for r in rows])


@app.get("/api/v1/admin/users/{user_id}", response_model=None)
def get_user(user_id: str) -> dict[str, Any] | JSONResponse:
    row = _get_user_by_id(user_id)
    if not row:
        return fail("用户不存在", code="NOT_FOUND")
    return ok(_user_to_api(row))


@app.post("/api/v1/admin/users", response_model=None)
def create_user(req: UserCreateRequest) -> dict[str, Any] | JSONResponse:
    username = req.username.strip()
    if _get_user_by_username(username):
        return fail("用户名已存在", code="BAD_REQUEST")
    role = (req.role or "VIEWER").upper()
    if role not in ("ADMIN", "OPERATOR", "VIEWER"):
        role = "VIEWER"
    user_id = "user-" + uuid.uuid4().hex[:12]
    hashed = _hash_password(req.password or f"{username}123")
    _execute(
        """
        INSERT INTO tg_user (user_id, username, display_name, email, role, status, password_hash, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, 'ACTIVE', %s, NOW(), NOW())
        """,
        (user_id, username, req.displayName or username, req.email, role, hashed),
    )
    row = _get_user_by_id(user_id)
    _record_audit("USER_CREATED", "admin", user_id, f"username={username} role={role}")
    return ok(_user_to_api(row or {}))


@app.put("/api/v1/admin/users/{user_id}", response_model=None)
async def update_user(user_id: str, request: Request) -> dict[str, Any] | JSONResponse:
    row = _get_user_by_id(user_id)
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
    return ok(_user_to_api(_get_user_by_id(user_id) or row))


@app.delete("/api/v1/admin/users/{user_id}", response_model=None)
def delete_user(user_id: str) -> dict[str, Any] | JSONResponse:
    rows = _execute("DELETE FROM tg_user WHERE user_id = %s", (user_id,))
    if rows <= 0:
        return fail("用户不存在", code="NOT_FOUND")
    _record_audit("USER_DELETED", "admin", user_id, "")
    return ok({"deleted": True, "userId": user_id})


@app.put("/api/v1/admin/users/{user_id}/password", response_model=None)
async def set_user_password(user_id: str, request: Request) -> dict[str, Any] | JSONResponse:
    row = _get_user_by_id(user_id)
    if not row:
        return fail("用户不存在", code="NOT_FOUND")
    body = await request.json()
    password = body.get("password") or body.get("newPassword") or ""
    if len(password) < 6:
        return fail("密码长度至少为 6 位", code="BAD_REQUEST")
    _execute("UPDATE tg_user SET password_hash = %s, updated_at = NOW() WHERE user_id = %s", (_hash_password(password), user_id))
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
