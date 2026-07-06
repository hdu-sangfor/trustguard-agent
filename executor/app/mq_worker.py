"""
兼容层：历史单测与文档仍 `import app.mq_worker`。

MQ 消费逻辑已合并至 `app.mq_execute_consumer`；生产环境请使用 **`python -m app.mq_agent_daemon`**
消费 **`MQ_TOPIC_AGENT`**（默认 `execute_tasks_agent`）。本模块不再提供 `run_worker`。
"""
from __future__ import annotations

from app.mq_execute_consumer import process_mq_execute_task_body


async def _handle_message(body: bytes) -> None:
    await process_mq_execute_task_body(body, log_role="mq_worker_compat")
