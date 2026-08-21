from __future__ import annotations

from typing import Any

from app.domain.models import AlertTriageDraft
from app.workflows.alert_triage.intent import parse_intent
from app.workflows.alert_triage.policy import validate_draft


class AlertTriageWorkflowAdapter:
    workflow_id = "alert_triage"

    def build_draft(self, message: str) -> dict[str, Any]:
        return parse_intent(message)

    def validate(self, draft: dict[str, Any]) -> tuple[list[str], list[str]]:
        return validate_draft(draft)

    def build_model(self, draft: dict[str, Any]) -> AlertTriageDraft:
        return AlertTriageDraft.model_validate(draft)
