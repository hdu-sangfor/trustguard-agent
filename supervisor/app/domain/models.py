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
    conversation_id: str | None = None


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


class ConsumeDraftRequest(ApiModel):
    confirmation_token: str = Field(min_length=1)


class ConsumeDraftResponse(ApiModel):
    draft_id: str
    conversation_id: str
    draft: PentestDraft


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
