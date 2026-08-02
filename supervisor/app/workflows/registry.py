from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from app.domain.models import WorkflowCapability
from app.workflows.base import WorkflowAdapter
from app.workflows.pentest.adapter import PentestWorkflowAdapter


@dataclass(frozen=True)
class RegisteredWorkflow:
    adapter: WorkflowAdapter
    capability: WorkflowCapability


class WorkflowRegistry:
    def __init__(self) -> None:
        self._items: dict[str, RegisteredWorkflow] = {}

    def register(self, adapter: WorkflowAdapter, *, title: str, intents: list[str]) -> None:
        self._items[adapter.workflow_id] = RegisteredWorkflow(
            adapter=adapter,
            capability=WorkflowCapability(
                workflow_id=adapter.workflow_id,
                title=title,
                intents=intents,
            ),
        )

    def resolve(self, workflow_id: str, message: str) -> RegisteredWorkflow:
        requested = (workflow_id or "auto").strip().lower()
        if requested == "auto":
            # The registry owns intent routing. New workflows add their own
            # deterministic or model-assisted matcher here without changing
            # the Supervisor graph.
            requested = "pentest"
        item = self._items.get(requested)
        if item is None:
            raise HTTPException(status_code=422, detail=f"unsupported workflow: {requested}")
        return item

    def capabilities(self) -> list[WorkflowCapability]:
        return [item.capability for item in self._items.values()]


workflow_registry = WorkflowRegistry()
workflow_registry.register(
    PentestWorkflowAdapter(),
    title="Authorized penetration testing",
    intents=["pentest", "penetration-test", "security-assessment"],
)
