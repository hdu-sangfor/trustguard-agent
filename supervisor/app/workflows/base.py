from __future__ import annotations

from typing import Any, Protocol


class WorkflowAdapter(Protocol):
    workflow_id: str

    def build_draft(self, message: str) -> dict[str, Any]: ...
    def validate(self, draft: dict[str, Any]) -> tuple[list[str], list[str]]: ...
