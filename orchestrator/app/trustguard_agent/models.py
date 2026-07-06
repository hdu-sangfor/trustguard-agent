from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    task_id: str = "demo-task"
    target: str
    objective: str = "Assess the target and produce concise security findings."
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["http_probe", "fingerprint", "risk_review"]
    )
    allowed_target: str | None = None
    max_steps: int = 6


class AgentRunResponse(BaseModel):
    status: str
    task_id: str
    target: str
    summary: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)

