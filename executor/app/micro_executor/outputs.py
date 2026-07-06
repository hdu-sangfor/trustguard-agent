"""
双轨输出之 Agent 轨（S-01）：stdout 仅输出紧凑 JSON，按**字符预算**截断（默认约 500 Token，4 字/Token 保守估计）。
编排器轨 16KiB 由现有 Executor HTTP 层负责，不在此模块重复实现。
"""
from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

AGENT_SUMMARY_SCHEMA_VERSION = 1
_DEFAULT_TOKEN_ESTIMATE = 500
_DEFAULT_CHARS_PER_TOKEN = 4

# 与运维文档、`GET /health` → `v1_execution_plane.agent_stdout_summary_default_max_chars` 对照时同源键名
MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV = "MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS"


def _default_max_agent_chars() -> int:
    raw = (os.getenv(MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV) or "").strip()
    if raw:
        try:
            return max(256, int(raw))
        except ValueError:
            pass
    return _DEFAULT_TOKEN_ESTIMATE * _DEFAULT_CHARS_PER_TOKEN


class AgentSummaryJSON(BaseModel):
    """
    供 run.py 经 SDK 构造后 `serialize_for_agent_stdout` 打印到 stdout。
    禁止额外字段，防止 Agent 侧键名注入污染。
    """

    model_config = ConfigDict(extra="forbid")

    v: int = Field(default=AGENT_SUMMARY_SCHEMA_VERSION, ge=1)
    skill_id: str
    status: str
    artifact_ref: str = ""
    summary: str = ""
    highlights: dict[str, Any] = Field(default_factory=dict)
    content_truncated: bool = False
    warning: str | None = None


def serialize_for_agent_stdout(
    body: AgentSummaryJSON,
    *,
    max_chars: int | None = None,
) -> str:
    """
    返回单行紧凑 UTF-8 JSON 字符串；超长则收缩 summary / highlights 并置 `content_truncated`。
    """
    limit = max_chars if max_chars is not None else _default_max_agent_chars()
    if limit < 64:
        limit = 64

    def pack(b: AgentSummaryJSON) -> str:
        return json.dumps(
            b.model_dump(exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    current = body.model_copy(deep=True)
    s = pack(current)
    if len(s) <= limit:
        return s

    current.content_truncated = True
    current.warning = "Warning: Content Truncated"
    # 先清空 highlights，再逐步缩短 summary
    current.highlights = {}
    s = pack(current)
    if len(s) <= limit:
        return s

    current.summary = ""
    s = pack(current)
    if len(s) <= limit:
        return s

    lo, hi = 0, len(body.summary)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        current.summary = body.summary[:mid]
        s = pack(current)
        if len(s) <= limit:
            best = s
            lo = mid + 1
        else:
            hi = mid - 1
    if best:
        return best

    # 极端：仍超长则最小骨架
    minimal = AgentSummaryJSON(
        v=AGENT_SUMMARY_SCHEMA_VERSION,
        skill_id=body.skill_id[:120],
        status=body.status[:32],
        artifact_ref=body.artifact_ref[:256],
        content_truncated=True,
        warning="Warning: Content Truncated",
    )
    s = pack(minimal)
    if len(s) <= limit:
        return s
    return s[:limit]
