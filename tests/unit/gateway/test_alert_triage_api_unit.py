import importlib
import sys

import pytest

from tests.paths import REPO_ROOT


def _load_gateway_main():
    gateway_root = str(REPO_ROOT / "gateway")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, gateway_root)
    try:
        return importlib.import_module("app.main")
    finally:
        if gateway_root in sys.path:
            sys.path.remove(gateway_root)


def test_triage_task_api_contract_converts_internal_result_to_ui_fields():
    gw = _load_gateway_main()

    task = gw._triage_task_to_api(
        {
            "task_id": "at-1",
            "alert_uuid": "alert-1",
            "status": "DONE",
            "enable_rag": True,
            "alert": {"name": "可疑 PowerShell", "severity": "high", "proofType": 2},
            "whitelist_matches": [{"id": "wl-1", "name": "approved-script"}],
            "related_incidents": [{"uuid": "inc-1", "title": "关联事件", "severity": "medium"}],
            "rag_degraded": True,
            "validation_errors": ["invalid confidence type"],
            "result": {
                "verdict": "suspicious",
                "confidence": 0.4,
                "severity": "high",
                "summary": "需人工复核",
                "reasoning": "证据不足",
                "recommended_actions": [{
                    "action": "人工确认后隔离主机",
                    "rationale": "存在横向移动迹象",
                    "execution_level": "manual_confirm",
                }],
                "missing_evidence": ["process_tree"],
                "enrichment_evidence": ["file_hash"],
                "rag_citations": [{"chunk_id": "c1"}],
                "finished_at": "2026-08-03T08:00:00Z",
            },
        },
        {"created_at": "2026-08-03T07:00:00Z", "updated_at": "2026-08-03T08:00:00Z"},
    )

    assert task["taskId"] == "at-1"
    assert task["alertUuid"] == "alert-1"
    assert task["alertSummary"]["severity"] == 4
    assert task["alertSummary"]["proofType"] == 2
    assert task["relatedIncidents"] == [{"uuId": "inc-1", "name": "关联事件", "severity": 3}]
    assert task["matchedWhitelists"][0]["id"] == "wl-1"
    assert task["recommendedActions"] == [{
        "action": "人工确认后隔离主机",
        "label": "人工确认后隔离主机",
        "category": "manual_required",
        "description": "存在横向移动迹象",
    }]
    assert task["missingEvidence"] == ["process_tree"]
    assert task["enrichmentEvidence"] == ["file_hash"]
    assert task["ragDegraded"] is True
    assert task["errors"] == ["invalid confidence type"]


def test_triage_task_api_contract_accepts_official_xdr_uuid_field():
    gw = _load_gateway_main()
    task = gw._triage_task_to_api(
        {
            "task_id": "at-official-fields",
            "alert_uuid": "alert-1",
            "status": "DONE",
            "related_incidents": [{"uuId": "incident-1", "name": "关联事件"}],
            "result": {"verdict": "true_positive", "confidence": 0.9},
        }
    )
    assert task["relatedIncidents"][0]["uuId"] == "incident-1"


@pytest.mark.asyncio
async def test_create_triage_returns_without_waiting_for_run(monkeypatch):
    gw = _load_gateway_main()
    scheduled = []
    inserts = []

    monkeypatch.setattr(gw, "_execute", lambda sql, params=(): inserts.append((sql, params)) or 1)
    monkeypatch.setattr(gw, "_get_task_row", lambda task_id: {
        "task_id": task_id,
        "target": "alert-1",
        "status": "PENDING",
        "created_at": "2026-08-03T07:00:00Z",
    })

    async def fake_orch(method, path, **kwargs):
        assert method == "POST"
        assert path.endswith("/tasks")
        return {"status": "PENDING"}

    monkeypatch.setattr(gw, "_orch", fake_orch)
    monkeypatch.setattr(gw.asyncio, "create_task", lambda coro: scheduled.append(coro))

    response = await gw.create_alert_triage_task(gw.CreateAlertTriageRequest(alert_uuid="alert-1"))
    for coro in scheduled:
        coro.close()

    assert response["code"] == "0"
    assert response["data"]["status"] == "PENDING"
    assert len(scheduled) == 1
    assert len(inserts) == 1
