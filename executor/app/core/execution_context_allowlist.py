"""
Executor 侧：与编排器 `orchestrator/app/core/agent_context.EXECUTION_CONTEXT_ALLOWED_KEYS` **必须**保持一致。

MQ Worker 校验 `MQExecuteTaskMessage.context` 时使用本模块；双端白名单单测见仓库 `tests/contracts/test_execution_context_allowlist_sync_unit.py`。
"""
from __future__ import annotations

# r4f-e：与编排器单一语义对齐；扩展键须 **同时** 改编排器 `agent_context.py` 与本 frozenset，并跑对齐单测。
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
