from __future__ import annotations

from app.workflows.alert_triage.intent import parse_intent


def test_alert_triage_intent_extracts_uuid_scenario_and_rag_preference():
    draft = parse_intent(
        "请研判告警 alert-tp-webshell-001，场景ID: webshell-true-positive，并启用RAG"
    )

    assert draft == {
        "workflow_id": "alert_triage",
        "alert_uuid": "alert-tp-webshell-001",
        "scenario_id": "webshell-true-positive",
        "enable_rag": True,
        "caller_notes": "请研判告警 alert-tp-webshell-001，场景ID: webshell-true-positive，并启用RAG",
    }


def test_alert_triage_intent_keeps_empty_uuid_for_clarification():
    draft = parse_intent("请做一次告警研判")

    assert draft["alert_uuid"] == ""
    assert draft["enable_rag"] is False
