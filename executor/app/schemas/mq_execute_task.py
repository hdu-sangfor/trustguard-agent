"""
MQ 执行任务消息的固定 Schema，与 orchestrator 双端共用同一契约。

Worker 消费 execute_tasks 队列时先用本 Schema 校验，再转为 SkillRequest 走现有执行路径；
确保 task_id、allowed_target、todo_id、agent_role 等安全字段一致，防止越权。
"""
from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.execution_context_allowlist import EXECUTION_CONTEXT_ALLOWED_KEYS
from app.execution_kind import resolve_execution_kind
from app.models import SkillRequest


class MQExecuteTaskMessage(BaseModel):
    """
    执行任务队列消息体（固定字段，与编排器发布格式一致）。
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
    """校验并返回 MQ 执行任务消息；校验失败抛出 ValueError。Worker 消费后调用。"""
    return MQExecuteTaskMessage.model_validate(payload)


def mq_message_to_skill_request(msg: MQExecuteTaskMessage) -> SkillRequest:
    """
    将校验后的 MQ 消息转为 SkillRequest，供现有 /v1/execute 逻辑复用。
    幂等：执行完成后写 Evidence 时应携带 msg.request_id，由 Evidence 或调用方以 request_id 做幂等/去重。
    """
    return SkillRequest(
        task_id=msg.task_id,
        skill_id=msg.skill_id,
        target=msg.target,
        params=msg.params,
        allowed_target=msg.allowed_target,
        context=dict(msg.context),
        request_id=msg.request_id,
        execution_kind=msg.execution_kind,
    )
