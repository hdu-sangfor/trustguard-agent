from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class WorkflowAdapter(Protocol):
    workflow_id: str

    def build_draft(self, message: str) -> dict[str, Any]: ...
    def validate(self, draft: dict[str, Any]) -> tuple[list[str], list[str]]: ...
    def build_model(self, draft: dict[str, Any]) -> BaseModel: ...
