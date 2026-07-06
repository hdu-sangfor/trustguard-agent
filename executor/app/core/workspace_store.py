from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json
import os
import uuid


WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/data/workspace"))
REF_PREFIX = "wsref:"


def _safe_name(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "unknown"
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in raw)


def _to_workspace_relative(path: Path) -> str:
    """将路径序列化为相对 WORKSPACE_ROOT 的标准字符串。"""
    try:
        rel = path.resolve().relative_to(WORKSPACE_ROOT.resolve())
        return rel.as_posix()
    except Exception:
        return path.as_posix()


def _artifact_base_from_input(raw_base: Path | str) -> Path:
    """
    兼容旧版绝对路径与新版相对路径：
    - 绝对路径：直接使用（但仍会被 resolve）
    - 相对路径：按 WORKSPACE_ROOT 解释
    """
    p = Path(raw_base)
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p
    return p.resolve()


def find_latest_katana_urls_file(task_id: str) -> Path | None:
    """
    查找任务目录下最近一次修改过的、非空的 discovery/katana_urls.txt。
    用于外层 docker/子进程超时后，仍可根据已落盘结果将 katana 标为成功（与卷延迟/杀进程竞态对齐）。
    """
    base = WORKSPACE_ROOT / _safe_name(task_id) / "web-vuln"
    if not base.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    try:
        for run_dir in base.iterdir():
            if not run_dir.is_dir():
                continue
            ku = run_dir / "discovery" / "katana_urls.txt"
            if ku.is_file() and ku.stat().st_size > 0:
                candidates.append((ku.stat().st_mtime, ku))
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def count_http_url_lines(path: Path) -> int:
    n = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    for line in text.splitlines():
        t = line.strip()
        if t.startswith(("http://", "https://")):
            n += 1
    return n


def prepare_artifact_slot(*, task_id: str, phase: str, skill_id: str) -> tuple[str, Path, str]:
    """
    在执行 skill 之前预创建与 write_artifact 相同布局的目录，并返回 (wsref, base_path, event_id)。
    技能可将大文件写入 base_path，执行结束后再 write_artifact(..., artifact_base=base) 复用同一目录。
    """
    task_key = _safe_name(task_id)
    phase_key = _safe_name(phase or "UNKNOWN")
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    event_key = f"{event_id}_{_safe_name(skill_id)}"
    base = WORKSPACE_ROOT / task_key / "artifacts" / phase_key / event_key
    base.mkdir(parents=True, exist_ok=True)
    ref = f"{REF_PREFIX}{task_key}/{phase_key}/{event_key}"
    return ref, base.resolve(), event_id


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
    artifact_base: Path | None = None,
    artifact_ref_precomputed: str | None = None,
) -> str:
    if artifact_base is not None:
        base = _artifact_base_from_input(artifact_base)
        ref = (artifact_ref_precomputed or "").strip()
        if not ref.startswith(REF_PREFIX):
            raise ValueError("artifact_ref_precomputed must be wsref: when using artifact_base")
        event_key = base.name
        event_id = event_key.split("_")[0] if "_" in event_key else event_key
    else:
        task_key = _safe_name(task_id)
        phase_key = _safe_name(phase or "UNKNOWN")
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        event_key = f"{event_id}_{_safe_name(skill_id)}"
        base = (
            WORKSPACE_ROOT
            / task_key
            / "artifacts"
            / phase_key
            / event_key
        )
        base.mkdir(parents=True, exist_ok=True)
        ref = f"{REF_PREFIX}{task_key}/{phase_key}/{event_key}"

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
    return ref


def resolve_artifact_ref(artifact_ref: str) -> Path | None:
    ref = (artifact_ref or "").strip()
    if not ref:
        return None
    # 兼容旧版本：若传绝对路径则仅允许在 WORKSPACE_ROOT 下
    p = Path(ref)
    if p.is_absolute():
        try:
            p.resolve().relative_to(WORKSPACE_ROOT.resolve())
            return p.resolve()
        except Exception:
            return None

    if not ref.startswith(REF_PREFIX):
        return None
    rel = ref[len(REF_PREFIX):].strip().replace("\\", "/")
    if not rel:
        return None
    parts = [x for x in rel.split("/") if x and x not in (".", "..")]
    if len(parts) != 3:
        return None
    return (WORKSPACE_ROOT / parts[0] / "artifacts" / parts[1] / parts[2]).resolve()


def read_artifact(artifact_ref: str, include_raw: bool = False) -> dict[str, Any] | None:
    base = resolve_artifact_ref(artifact_ref)
    if base is None or not base.exists():
        return None
    try:
        meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    try:
        req = json.loads((base / "request.json").read_text(encoding="utf-8"))
    except Exception:
        req = {}
    try:
        parsed = json.loads((base / "parsed.json").read_text(encoding="utf-8"))
    except Exception:
        parsed = {}

    payload: dict[str, Any] = {
        "artifact_ref": artifact_ref,
        "meta": meta,
        "request": req,
        "parsed": parsed,
    }
    if include_raw:
        payload["raw_stdout"] = (base / "raw.out").read_text(encoding="utf-8")
        payload["raw_stderr"] = (base / "raw.err").read_text(encoding="utf-8")
    return payload

