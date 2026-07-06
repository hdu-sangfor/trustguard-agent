"""execution_kind 协议（缺省推导 + 冲突拒绝）。"""
from __future__ import annotations

from typing import Literal

ExecutionKind = Literal["skill"]


def resolve_execution_kind(*, skill_id: str, execution_kind: str | None) -> ExecutionKind:
    """缺省为 skill；不再支持旧 agent 载体执行通道。"""
    _ = skill_id
    ek = execution_kind
    if ek is None:
        return "skill"
    if ek != "skill":
        raise ValueError("execution_kind must be 'skill' or omitted")
    return ek
