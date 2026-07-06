from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pymysql.cursors import DictCursor


log = logging.getLogger("trustguard.evidence")

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "trustguard")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "trustguard")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "trustguard_agent")
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/data/workspace")).resolve()

app = FastAPI(title="TrustGuard Evidence", version="1.0.0")


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
            rows = cur.fetchall()
            return list(rows or [])


def _execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            return int(cur.execute(sql, params))


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def _json_loads(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _limit(value: int | None, default: int, max_value: int = 1000) -> int:
    if value is None or value <= 0:
        return default
    return min(value, max_value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dt_to_iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _parse_mysql_datetime(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1] + "+00:00").astimezone(timezone.utc).replace(tzinfo=None)
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _safe_component(value: str, fallback: str) -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = text.strip("._")
    return text[:120] or fallback


def _error_body(code: str, message: str, *, field_errors: dict[str, str] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "trace_id": "err-" + uuid.uuid4().hex[:12],
    }
    if field_errors:
        body["field_errors"] = field_errors
    return body


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors: dict[str, str] = {}
    code = "VALIDATION_ERROR"
    status = 422
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []) if p != "body") or "body"
        errors[loc] = str(err.get("msg", "invalid value"))
        if err.get("type") == "json_invalid":
            code = "BAD_JSON"
            status = 400
    return JSONResponse(
        status_code=status,
        content=_error_body(code, "request validation failed", field_errors=errors),
    )


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body("HTTP_ERROR", str(exc.detail or "request failed")),
    )


class EventIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(validation_alias=AliasChoices("task_id", "taskId"), min_length=1)
    timestamp: str = Field(validation_alias=AliasChoices("timestamp", "ts"), min_length=1)
    event_type: str = Field(validation_alias=AliasChoices("event_type", "eventType"), min_length=1)
    source_module: str = Field(validation_alias=AliasChoices("source_module", "sourceModule"), min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    run_started_at: str | None = Field(default=None, validation_alias=AliasChoices("run_started_at", "runStartedAt"))
    run_finished_at: str | None = Field(default=None, validation_alias=AliasChoices("run_finished_at", "runFinishedAt"))
    run_duration_ms: int | None = Field(default=None, validation_alias=AliasChoices("run_duration_ms", "runDurationMs"))


class CheckpointIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    current_phase: str = Field(validation_alias=AliasChoices("current_phase", "currentPhase"), min_length=1)
    status: str = Field(min_length=1)
    target_context: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("target_context", "targetContext"))
    history_summary: str = Field(default="", validation_alias=AliasChoices("history_summary", "historySummary"))
    name: str | None = None
    target: str | None = None
    description: str | None = None
    phase_start_at: str | None = Field(default=None, validation_alias=AliasChoices("phase_start_at", "phaseStartAt"))
    current_phase_duration_limit_sec: int | None = Field(
        default=None,
        validation_alias=AliasChoices("current_phase_duration_limit_sec", "currentPhaseDurationLimitSec"),
    )
    llm_input_tokens_total: int | None = Field(default=None, validation_alias=AliasChoices("llm_input_tokens_total", "llmInputTokensTotal"))
    llm_output_tokens_total: int | None = Field(default=None, validation_alias=AliasChoices("llm_output_tokens_total", "llmOutputTokensTotal"))
    cumulative_cost_usd: float | None = Field(default=None, validation_alias=AliasChoices("cumulative_cost_usd", "cumulativeCostUsd"))


class ArtifactIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    skill_id: str = Field(validation_alias=AliasChoices("skill_id", "skillId"), min_length=1)
    summary: str | None = None
    filename: str | None = None
    content: str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        _query("SELECT 1 AS ok")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=_error_body("DB_UNAVAILABLE", str(exc))) from exc
    return {"status": "ok", "service": "evidence"}


@app.post("/v1/events")
def ingest_event(event: EventIn) -> dict[str, Any]:
    event_id = "evt-" + uuid.uuid4().hex[:12]
    duration = event.run_duration_ms
    if duration is None:
        raw = event.payload.get("duration_ms", event.payload.get("run_duration_ms"))
        try:
            parsed = int(raw) if raw is not None else None
            duration = parsed if parsed is not None and parsed >= 0 else None
        except (TypeError, ValueError):
            duration = None
    try:
        _execute(
            """
            INSERT INTO tg_trace_events
              (task_id, event_id, ts, event_type, source_module, payload,
               run_started_at, run_finished_at, run_duration_ms, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                event.task_id,
                event_id,
                event.timestamp,
                event.event_type,
                event.source_module,
                _json_dumps(event.payload),
                event.run_started_at,
                event.run_finished_at,
                duration,
            ),
        )
        return {"event_id": event_id, "accepted": True}
    except Exception:
        log.exception("failed to ingest event task_id=%s event_type=%s", event.task_id, event.event_type)
        return {"event_id": event_id, "accepted": False}


@app.get("/internal/tasks")
def list_tasks(limit: int = 200) -> list[dict[str, Any]]:
    max_rows = _limit(limit, 200)
    merged: dict[str, dict[str, Any]] = {}

    for row in _query(
        """
        SELECT task_id, name, description, target, status, current_phase, updated_at
        FROM tg_task
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        (max_rows,),
    ):
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        merged[task_id] = {
            "task_id": task_id,
            "name": row.get("name") or "",
            "description": row.get("description") or "",
            "target": row.get("target") or "",
            "status": row.get("status") or "",
            "current_phase": row.get("current_phase") or "",
            "updated_at": _dt_to_iso(row.get("updated_at")),
        }

    for row in _query(
        """
        SELECT task_id, name, description, target, status, current_phase, updated_at
        FROM tg_task_checkpoint
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        (max_rows,),
    ):
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        item = merged.setdefault(task_id, {"task_id": task_id})
        for key in ("name", "description", "target", "status", "current_phase"):
            if row.get(key):
                item[key] = row.get(key)
        item["updated_at"] = item.get("updated_at") or _dt_to_iso(row.get("updated_at"))

    if len(merged) < max_rows:
        for row in _query(
            """
            SELECT task_id, context_json, updated_at
            FROM tg_task_context
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (max_rows * 3,),
        ):
            task_id = str(row.get("task_id") or "")
            if not task_id:
                continue
            ctx = _json_loads(row.get("context_json"))
            item = merged.setdefault(
                task_id,
                {
                    "task_id": task_id,
                    "name": "",
                    "description": "",
                    "target": "",
                    "status": "RUNNING",
                    "current_phase": "RECON",
                    "updated_at": _dt_to_iso(row.get("updated_at")),
                },
            )
            if isinstance(ctx, dict) and not item.get("target") and isinstance(ctx.get("target"), str):
                item["target"] = ctx.get("target")
            if len(merged) >= max_rows:
                break

    if len(merged) < max_rows:
        for row in _query(
            """
            SELECT task_id, event_type, payload, created_at
            FROM tg_trace_events
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (max_rows * 10,),
        ):
            task_id = str(row.get("task_id") or "")
            if not task_id:
                continue
            payload = _json_loads(row.get("payload"))
            item = merged.setdefault(
                task_id,
                {
                    "task_id": task_id,
                    "name": "",
                    "description": "",
                    "target": "",
                    "status": "RUNNING",
                    "current_phase": "RECON",
                    "updated_at": _dt_to_iso(row.get("created_at")),
                },
            )
            if str(row.get("event_type") or "").upper() == "TASK_COMPLETED":
                item["status"] = "DONE"
            elif str(row.get("event_type") or "").upper() == "ERROR" and not item.get("status"):
                item["status"] = "FAILED"
            if isinstance(payload, dict) and isinstance(payload.get("phase"), str):
                item["current_phase"] = payload["phase"]
            if len(merged) >= max_rows:
                break

    return list(merged.values())[:max_rows]


@app.get("/internal/tasks/{task_id}/events")
def list_task_events(task_id: str, limit: int = 500) -> list[dict[str, Any]]:
    max_rows = _limit(limit, 500)
    rows = _query(
        """
        SELECT event_id, task_id, ts, event_type, source_module, payload,
               run_started_at, run_finished_at, run_duration_ms
        FROM tg_trace_events
        WHERE task_id = %s
        ORDER BY ts ASC, id ASC
        LIMIT %s
        """,
        (task_id, max_rows),
    )
    return [
        {
            "event_id": row.get("event_id") or "",
            "task_id": row.get("task_id") or "",
            "timestamp": row.get("ts") or "",
            "event_type": row.get("event_type") or "",
            "source_module": row.get("source_module") or "",
            "payload": _json_loads(row.get("payload")),
            "run_started_at": row.get("run_started_at") or "",
            "run_finished_at": row.get("run_finished_at") or "",
            "run_duration_ms": row.get("run_duration_ms") or 0,
        }
        for row in rows
    ]


@app.get("/internal/tasks/{task_id}/context")
def get_context(task_id: str) -> dict[str, Any]:
    rows = _query("SELECT context_json FROM tg_task_context WHERE task_id = %s", (task_id,))
    if not rows:
        return {}
    data = _json_loads(rows[0].get("context_json"))
    return data if isinstance(data, dict) else {}


@app.put("/internal/tasks/{task_id}/context")
async def put_context(task_id: str, request: Request) -> dict[str, str]:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail=_error_body("VALIDATION_ERROR", "context must be a JSON object"))
    _execute(
        """
        INSERT INTO tg_task_context (task_id, context_json, updated_at)
        VALUES (%s, %s, NOW())
        ON DUPLICATE KEY UPDATE context_json = VALUES(context_json), updated_at = NOW()
        """,
        (task_id, _json_dumps(body)),
    )
    return {"status": "ok"}


@app.get("/internal/tasks/{task_id}/checkpoint", response_model=None)
def get_checkpoint(task_id: str) -> dict[str, Any] | Response:
    rows = _query(
        """
        SELECT task_id, current_phase, status, target_context_json, history_summary,
               name, target, description, phase_start_at, current_phase_duration_limit_sec,
               llm_input_tokens_total, llm_output_tokens_total, cumulative_cost_usd, updated_at
        FROM tg_task_checkpoint
        WHERE task_id = %s
        """,
        (task_id,),
    )
    if not rows:
        return Response(status_code=204)
    row = rows[0]
    body: dict[str, Any] = {
        "task_id": row.get("task_id"),
        "current_phase": row.get("current_phase"),
        "status": row.get("status"),
        "target_context": _json_loads(row.get("target_context_json")),
        "history_summary": row.get("history_summary") or "",
        "name": row.get("name") or "",
        "target": row.get("target") or "",
        "description": row.get("description") or "",
        "updated_at": _dt_to_iso(row.get("updated_at")),
    }
    if row.get("phase_start_at") is not None:
        body["phase_start_at"] = _dt_to_iso(row.get("phase_start_at"))
    if row.get("current_phase_duration_limit_sec") is not None:
        body["current_phase_duration_limit_sec"] = row.get("current_phase_duration_limit_sec")
    if row.get("llm_input_tokens_total") is not None:
        body["llm_input_tokens_total"] = row.get("llm_input_tokens_total")
    if row.get("llm_output_tokens_total") is not None:
        body["llm_output_tokens_total"] = row.get("llm_output_tokens_total")
    if row.get("cumulative_cost_usd") is not None:
        body["cumulative_cost_usd"] = row.get("cumulative_cost_usd")
    return body


@app.put("/internal/tasks/{task_id}/checkpoint")
async def put_checkpoint(task_id: str, request: Request) -> dict[str, str]:
    raw = await request.json()
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail=_error_body("VALIDATION_ERROR", "checkpoint must be a JSON object"))
    body = CheckpointIn.model_validate(raw)

    rows = _query("SELECT id FROM tg_task_checkpoint WHERE task_id = %s", (task_id,))
    phase_touched = any(k in raw for k in ("phase_start_at", "phaseStartAt", "current_phase_duration_limit_sec", "currentPhaseDurationLimitSec"))
    fields: dict[str, Any] = {
        "current_phase": body.current_phase,
        "status": body.status,
        "target_context_json": _json_dumps(body.target_context),
        "history_summary": body.history_summary or "",
        "name": body.name,
        "target": body.target,
        "description": body.description,
    }
    if phase_touched:
        fields["phase_start_at"] = _parse_mysql_datetime(body.phase_start_at)
        fields["current_phase_duration_limit_sec"] = body.current_phase_duration_limit_sec if (body.current_phase_duration_limit_sec or 0) >= 0 else None
    if any(k in raw for k in ("llm_input_tokens_total", "llmInputTokensTotal")):
        fields["llm_input_tokens_total"] = body.llm_input_tokens_total
    if any(k in raw for k in ("llm_output_tokens_total", "llmOutputTokensTotal")):
        fields["llm_output_tokens_total"] = body.llm_output_tokens_total
    if any(k in raw for k in ("cumulative_cost_usd", "cumulativeCostUsd")):
        fields["cumulative_cost_usd"] = body.cumulative_cost_usd

    if rows:
        assignments = ", ".join(f"{key} = %s" for key in fields)
        _execute(
            f"UPDATE tg_task_checkpoint SET {assignments}, updated_at = NOW() WHERE task_id = %s",
            tuple(fields.values()) + (task_id,),
        )
    else:
        insert_fields = {"task_id": task_id, **fields}
        columns = ", ".join(insert_fields.keys()) + ", updated_at"
        placeholders = ", ".join(["%s"] * len(insert_fields)) + ", NOW()"
        _execute(
            f"INSERT INTO tg_task_checkpoint ({columns}) VALUES ({placeholders})",
            tuple(insert_fields.values()),
        )
    return {"status": "ok"}


@app.put("/internal/tasks/{task_id}/artifacts")
def put_artifacts(task_id: str, body: ArtifactIn) -> dict[str, str]:
    task_dir = WORKSPACE_ROOT / _safe_component(task_id, "task")
    artifacts_dir = task_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    written_filename = None
    if body.filename and body.content is not None:
        skill_part = _safe_component(body.skill_id, "skill")
        file_part = _safe_component(body.filename, "artifact.txt")
        written_filename = f"{skill_part}_{file_part}"
        (artifacts_dir / written_filename).write_text(body.content or "", encoding="utf-8")

    if body.summary:
        summary_path = task_dir / "artifacts_summary.jsonl"
        line = {
            "skill_id": body.skill_id,
            "summary": body.summary,
            "timestamp": _now_iso(),
        }
        if written_filename:
            line["filename"] = written_filename
        with summary_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"status": "ok"}


@app.get("/internal/tasks/{task_id}/artifacts-summary")
def get_artifacts_summary(task_id: str, limit: int = 500) -> list[dict[str, Any]]:
    max_rows = _limit(limit, 500, max_value=5000)
    summary_path = WORKSPACE_ROOT / _safe_component(task_id, "task") / "artifacts_summary.jsonl"
    if not summary_path.is_file():
        return []
    try:
        lines = summary_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max_rows:]:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out
