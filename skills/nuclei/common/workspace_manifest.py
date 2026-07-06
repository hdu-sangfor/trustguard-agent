"""
Shared workspace layout for web-vuln split pipeline (discovery → dispatch → scan).

Layout under WORKSPACE_ROOT:
  {task_id}/web-vuln/{run_id}/
    manifest.json
    discovery/katana_urls.txt
    discovery/dirsearch.json   (optional)
    chunks/chunk_NNNN.txt
    results/chunk_NNNN.jsonl
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def safe_task_id(s: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9_-]+", "_", (s or "task").strip())[:96]
    return t or "task"


def run_root(workspace_root: str, task_id: str, run_id: str) -> Path:
    return Path(workspace_root) / safe_task_id(task_id) / "web-vuln" / run_id


def discovery_dir(root: Path) -> Path:
    return root / "discovery"


def chunks_dir(root: Path) -> Path:
    return root / "chunks"


def results_dir(root: Path) -> Path:
    return root / "results"


def manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def context_fingerprint(auth_header: str, user_agent: str) -> str:
    raw = f"{auth_header.strip()}\n{user_agent.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def load_manifest(root: Path) -> dict[str, Any] | None:
    p = manifest_path(root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_manifest(root: Path, data: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest_path(root).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def chunk_rel_path(index: int) -> str:
    return f"chunks/chunk_{index:04d}.txt"


def result_rel_path(index: int) -> str:
    return f"results/chunk_{index:04d}.jsonl"


def build_manifest(
    *,
    task_id: str,
    run_id: str,
    chunk_size: int,
    total_chunks: int,
    chunks_meta: list[dict[str, Any]],
    context_block: dict[str, Any],
    discovery_rel: str,
    determinism_version: str,
    ttl_seconds: int = 3600,
) -> dict[str, Any]:
    now = int(time.time())
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "run_id": run_id,
        "created_at": now,
        "ttl_deadline_epoch": now + ttl_seconds,
        "determinism_version": determinism_version,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "discovery_ref": discovery_rel,
        "context": context_block,
        "chunks": chunks_meta,
    }
