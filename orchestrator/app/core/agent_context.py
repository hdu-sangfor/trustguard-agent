from __future__ import annotations

from typing import Any, Dict

# 单一来源：Executor 上下文允许透传的字段白名单。
# MQ Worker 镜像：executor/app/core/execution_context_allowlist.py（须与本集合一致；单测 tests/contracts/test_execution_context_allowlist_sync_unit.py）。
EXECUTION_CONTEXT_ALLOWED_KEYS = frozenset(
    {
        "phase",
        "agent_role",
        "todo_id",
        "request_id",
        "plan_id",
        "target_scope_mode",
        "allowed_ports",
    }
)


def build_execute_context(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    """
    统一构建发往 Executor 的 context：
    - 仅保留 EXECUTION_CONTEXT_ALLOWED_KEYS 中的键；
    - None 或非 dict 情况下返回空 dict。
    """
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in EXECUTION_CONTEXT_ALLOWED_KEYS}
