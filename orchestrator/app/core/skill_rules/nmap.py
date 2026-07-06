"""
Nmap skill 纯规则编译：从 PlanItem.metadata 提取白名单参数，无 LLM。

约定（Planner/上游写入 metadata，无需自然语言解析）：
- `metadata.params` 或 `metadata.nmap_params`：dict，键须在白名单内；
- `timeout` 未给出时，默认 `min(constraints.timeout_seconds, 120)`，与 executor `execute.py` 硬顶对齐。
"""
from __future__ import annotations

from typing import Any

from app.plan_models import PlanItem

_ALLOWED = frozenset({"ports", "top_ports", "scripts", "extra", "args", "arguments", "timeout"})
_NMAP_HARD_TIMEOUT_DEFAULT = 120


def build_nmap_params(plan_item: PlanItem) -> dict[str, Any]:
    meta = plan_item.metadata or {}
    out: dict[str, Any] = {}
    for key in ("params", "nmap_params"):
        blob = meta.get(key)
        if not isinstance(blob, dict):
            continue
        for k, v in blob.items():
            if k in _ALLOWED:
                out[k] = v

    ts = int(plan_item.constraints.timeout_seconds)
    ts = max(1, min(ts, _NMAP_HARD_TIMEOUT_DEFAULT))
    out.setdefault("timeout", ts)
    return out
