from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.workflows.registry import workflow_registry


def test_registry_exposes_and_resolves_pentest_workflow():
    capabilities = workflow_registry.capabilities()

    assert [item.workflow_id for item in capabilities] == ["pentest"]
    assert workflow_registry.resolve("auto", "做一次已授权渗透").adapter.workflow_id == "pentest"


def test_registry_rejects_unknown_workflow():
    with pytest.raises(HTTPException) as exc:
        workflow_registry.resolve("alert-triage", "研判告警")

    assert exc.value.status_code == 422
