"""
将编排器下发的 discovery/output 路径解析为当前进程可见的磁盘路径。

背景：编排器可能在 Windows 上运行，拼出 D:\\...\\data\\evidence-workspace\\targets\\...；
技能容器内 WORKSPACE_ROOT=/data/workspace，且 POSIX 下 Path(\"D:/...\") 非绝对路径，会误解析到 cwd。
"""
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

    # Windows 盘符路径：在 Linux 容器内映射到卷挂载后的 WORKSPACE_ROOT 相对后缀
    if len(norm) > 2 and norm[1] == ":" and norm[2] == "/":
        for mk in _MARKERS:
            idx = low.find(mk)
            if idx >= 0:
                suffix = norm[idx + len(mk) :].lstrip("/")
                return (ws_path / suffix).resolve()
        return None

    # 已是工作区相对路径，如 targets/<key>/discovery
    return (ws_path / norm).resolve()
