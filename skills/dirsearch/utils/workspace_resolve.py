"""与 web-vuln-common/scripts/workspace_resolve.py 保持一致（dirsearch 镜像独立构建上下文）。"""
from __future__ import annotations

import os
from pathlib import Path

_MARKERS = (
    "data/evidence-workspace/",
    "data/executor-workspace/",
    "evidence-workspace/",
    "executor-workspace/",
)


def resolve_under_workspace(raw: str | None, workspace_root: str | None = None) -> Path | None:
    s = (raw or "").strip()
    if not s:
        return None
    norm = s.replace("\\", "/")
    low = norm.lower()
    ws = (workspace_root or os.getenv("WORKSPACE_ROOT", "/data/workspace") or "/data/workspace").strip()
    ws_path = Path(ws)

    if norm.startswith("/"):
        return Path(norm)

    if len(norm) > 2 and norm[1] == ":" and norm[2] == "/":
        for mk in _MARKERS:
            idx = low.find(mk)
            if idx >= 0:
                suffix = norm[idx + len(mk) :].lstrip("/")
                return (ws_path / suffix).resolve()
        return None

    return (ws_path / norm).resolve()
