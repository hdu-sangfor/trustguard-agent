from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class ActivityStep(ApiModel):
    id: str
    kind: Literal["analysis", "guard", "tool", "result", "progress"]
    title: str
    detail: str = ""
    status: Literal["pending", "running", "done", "blocked"] = "done"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class PentestDraft(ApiModel):
    name: str
    target: str
    description: str = ""
    business_background: str = ""
    extra_user_requirements: str = ""
    test_profile: Literal["safe", "standard", "aggressive"] = "safe"
    allow_exploit: bool = False
    allow_destructive_actions: bool = False
    max_duration_seconds: int = Field(default=900, ge=60, le=86400)


class DraftRequest(ApiModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    workflow_id: str = Field(default="auto", min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")


class DraftResponse(ApiModel):
    status: Literal["NEEDS_CLARIFICATION", "NEEDS_CONFIRMATION", "REJECTED", "READY"]
    conversation_id: str
    draft_id: str | None = None
    confirmation_token: str | None = None
    draft: PentestDraft | None = None
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assistant_message: str
    activities: list[ActivityStep] = Field(default_factory=list)
    workflow_id: str = "pentest"


class ConversationMessage(ApiModel):
    id: str = Field(min_length=1, max_length=160)
    role: Literal["user", "assistant"]
    text: str = Field(max_length=20000)
    activities: list[ActivityStep] = Field(default_factory=list)
    draft: PentestDraft | None = None
    confirmation_token: str | None = None
    task_id: str | None = Field(default=None, max_length=128)
    task_status: str | None = Field(default=None, max_length=32)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class ConversationMessageRequest(ApiModel):
    id: str = Field(min_length=1, max_length=160)
    role: Literal["user", "assistant"] = "assistant"
    text: str = Field(max_length=20000)
    activities: list[ActivityStep] = Field(default_factory=list)
    draft: PentestDraft | None = None
    confirmation_token: str | None = None
    task_id: str | None = Field(default=None, max_length=128)
    task_status: str | None = Field(default=None, max_length=32)


class ConversationResponse(ApiModel):
    conversation_id: str
    messages: list[ConversationMessage] = Field(default_factory=list)
    task_id: str | None = None


class ConversationSummary(ApiModel):
    conversation_id: str
    title: str = Field(max_length=200)
    preview: str = Field(default="", max_length=500)
    task_id: str | None = Field(default=None, max_length=128)
    task_status: str | None = Field(default=None, max_length=32)
    message_count: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str


class ProgressSummaryRequest(ApiModel):
    task_id: str = Field(min_length=1, max_length=128)
    task_status: str = Field(default="RUNNING", max_length=32)
    current_phase: str = Field(default="RECON", max_length=64)
    total_steps: int = Field(default=0, ge=0)
    recent_events: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    fallback_message: str = Field(default="任务仍在执行中。", max_length=2000)


class ProgressSummaryResponse(ApiModel):
    assistant_message: str


class ConsumeDraftRequest(ApiModel):
    confirmation_token: str = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ConsumeDraftResponse(ApiModel):
    draft_id: str
    conversation_id: str
    draft: PentestDraft
    confirmation_state: Literal["CLAIMED", "COMPLETED"] = "CLAIMED"
    task_id: str | None = None


class CompleteDraftRequest(ApiModel):
    draft_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=64)


class CompleteDraftResponse(ApiModel):
    draft_id: str
    confirmation_state: Literal["COMPLETED"] = "COMPLETED"
    task_id: str


class WorkflowCapability(ApiModel):
    workflow_id: str
    title: str
    intents: list[str] = Field(default_factory=list)


class SupervisorState(TypedDict, total=False):
    """Small serializable state passed through the LangGraph nodes."""

    message: str
    draft: dict[str, Any]
    missing_fields: list[str]
    warnings: list[str]
    activities: list[dict[str, Any]]
    status: str


class HealthResponse(ApiModel):
    status: str
    service: str
    llm_mode: str
    store_backend: str = "memory"
    conversation_store_backend: str = "memory"
