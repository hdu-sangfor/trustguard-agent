"""
MQ 执行任务消息的固定 Schema，与 executor 双端共用同一契约。

编排器发布到 execute_tasks 队列时使用本 Schema 构建并校验；
Worker 消费时使用相同结构校验，确保 task_id、allowed_target、todo_id、agent_role 等安全字段一致。
"""
from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.agent_context import EXECUTION_CONTEXT_ALLOWED_KEYS
from app.core.execution_kind import resolve_execution_kind


class MQExecuteTaskMessage(BaseModel):
    """
    执行任务队列消息体（固定字段，禁止随意扩展以免越权或注入）。
    """

    request_id: str
    task_id: str
    skill_id: str
    target: str
    params: dict[str, Any] = {}
    allowed_target: str
    context: dict[str, Any] = {}
    todo_id: str | None = None
    execution_kind: Literal["skill"] | None = Field(default=None)

    @field_validator("request_id", "task_id", "skill_id", "target", "allowed_target")
    @classmethod
    def non_empty_string(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("field must be non-empty")
        return str(v).strip()

    @field_validator("context", mode="before")
    @classmethod
    def context_only_allowed_keys(cls, v: Any) -> dict[str, Any]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("context must be a dict")
        return {k: val for k, val in v.items() if k in EXECUTION_CONTEXT_ALLOWED_KEYS}

    @model_validator(mode="after")
    def _normalize_execution_kind(self) -> Self:
        resolved = resolve_execution_kind(skill_id=self.skill_id, execution_kind=self.execution_kind)
        object.__setattr__(self, "execution_kind", resolved)
        return self


def validate_mq_execute_task_message(payload: dict[str, Any]) -> MQExecuteTaskMessage:
    """校验并返回 MQ 执行任务消息；校验失败抛出 ValueError。编排器发布前与 Worker 消费后均可调用。"""
    return MQExecuteTaskMessage.model_validate(payload)


def build_mq_execute_task_message(
    *,
    request_id: str,
    task_id: str,
    skill_id: str,
    target: str,
    params: dict[str, Any],
    allowed_target: str,
    context: dict[str, Any] | None = None,
    todo_id: str | None = None,
    execution_kind: Literal["skill"] | None = None,
) -> MQExecuteTaskMessage:
    """编排器侧：从当前执行参数构建 MQ 消息并校验，供 ExecutionDispatcher 发布到队列时使用。"""
    ctx = dict(context or {})
    return MQExecuteTaskMessage(
        request_id=request_id.strip(),
        task_id=task_id.strip(),
        skill_id=skill_id.strip(),
        target=target.strip(),
        params=params or {},
        allowed_target=allowed_target.strip(),
        context=ctx,
        todo_id=todo_id.strip() if todo_id else None,
        execution_kind=execution_kind,
    )
