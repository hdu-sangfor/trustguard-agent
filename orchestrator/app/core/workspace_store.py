from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json
import os
import uuid


WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/data/workspace"))


def _safe_name(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "unknown"
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in raw)


def _task_dir(task_id: str) -> Path:
    return WORKSPACE_ROOT / _safe_name(task_id)


def _to_workspace_relative(path: Path) -> str:
    """返回相对 WORKSPACE_ROOT 的路径字符串，避免把宿主机绝对路径写入上下文。"""
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def write_task_context(task_id: str, context: dict[str, Any]) -> None:
    task_dir = _task_dir(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / "task_context.json"
    path.write_text(json.dumps(context or {}, ensure_ascii=False, indent=2), encoding="utf-8")


def write_artifact(
    *,
    task_id: str,
    phase: str,
    skill_id: str,
    request_payload: dict[str, Any],
    status: str,
    duration_ms: int | None,
    raw_stdout: str | None,
    raw_stderr: str | None,
    parsed_artifacts: dict[str, Any] | None,
) -> str:
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    base = _task_dir(task_id) / "artifacts" / _safe_name(phase) / f"{event_id}_{_safe_name(skill_id)}"
    base.mkdir(parents=True, exist_ok=True)

    (base / "request.json").write_text(
        json.dumps(request_payload or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (base / "raw.out").write_text((raw_stdout or ""), encoding="utf-8")
    (base / "raw.err").write_text((raw_stderr or ""), encoding="utf-8")
    (base / "parsed.json").write_text(
        json.dumps(parsed_artifacts or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (base / "meta.json").write_text(
        json.dumps(
            {
                "event_id": event_id,
                "task_id": task_id,
                "phase": phase,
                "skill_id": skill_id,
                "status": status,
                "duration_ms": duration_ms,
                "created_at": datetime.utcnow().isoformat() + "Z",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return _to_workspace_relative(base)


def write_memory_parsed_artifact(task_id: str, event_id: str, parsed_artifacts: dict[str, Any]) -> str:
    """
    将「入 evidence 上下文的记忆快照」落在对应任务目录下，避免与 workspace 根目录混杂。

    路径：{WORKSPACE_ROOT}/{safe_task}/memory/memory-{safe_task}-{eid}-parsed.json
    （文件名保持历史模式，便于与旧脚本/日志对照；仅目录从根迁移到 task 内。）
    """
    safe_task = _safe_name(task_id)
    eid = (event_id or "unknown").strip().replace("/", "_").replace("\\", "_")
    filename = f"memory-{safe_task}-{eid}-parsed.json"
    mem_dir = _task_dir(task_id) / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    path = mem_dir / filename
    path.write_text(json.dumps(parsed_artifacts or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    return _to_workspace_relative(path)

