from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.workflows.registry import workflow_registry


def test_registry_exposes_and_resolves_supported_workflows():
    capabilities = workflow_registry.capabilities()

    assert [item.workflow_id for item in capabilities] == ["pentest", "alert_triage"]
    assert workflow_registry.resolve("auto", "做一次已授权渗透").adapter.workflow_id == "pentest"
    assert workflow_registry.resolve("auto", "请研判告警 alert-tp-webshell-001").adapter.workflow_id == "alert_triage"
    assert workflow_registry.resolve("alert-triage", "").adapter.workflow_id == "alert_triage"


def test_registry_rejects_unknown_workflow():
    with pytest.raises(HTTPException) as exc:
        workflow_registry.resolve("unknown-workflow", "研判告警")

    assert exc.value.status_code == 422
