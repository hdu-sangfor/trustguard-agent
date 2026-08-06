from __future__ import annotations

from app.workflows.alert_triage.policy import validate_draft


def test_alert_triage_policy_requires_well_formed_alert_uuid():
    assert validate_draft({"alert_uuid": ""})[0] == ["alert_uuid"]
    assert validate_draft({"alert_uuid": "incident-001"})[0] == ["alert_uuid"]
    assert validate_draft({"alert_uuid": "alert-tp-webshell-001"})[0] == []


def test_alert_triage_policy_marks_rag_as_supporting_evidence_only():
    missing, warnings = validate_draft({
        "alert_uuid": "alert-tp-webshell-001",
        "enable_rag": True,
    })

    assert missing == []
    assert len(warnings) == 1
    assert "XDR 原始证据" in warnings[0]
