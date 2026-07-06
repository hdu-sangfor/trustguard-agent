# 与编排器约定的 MQ 消息 Schema，供 Worker 消费时校验

from app.schemas.mq_execute_task import (
    MQExecuteTaskMessage,
    validate_mq_execute_task_message,
    mq_message_to_skill_request,
)

__all__ = [
    "MQExecuteTaskMessage",
    "validate_mq_execute_task_message",
    "mq_message_to_skill_request",
]
