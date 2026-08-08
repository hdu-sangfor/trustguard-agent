import importlib
import sys

import pytest
from fastapi.testclient import TestClient

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


def test_triage_routes_require_authentication():
    gw = _load_gateway_main()
    client = TestClient(gw.app)

    assert client.get("/api/v1/alert-triage/tasks").status_code == 401
    assert client.get("/api/v1/alert-triage/tasks/at-secret/events").status_code == 401
    assert client.post(
        "/api/v1/alert-triage/tasks",
        json={"alertUuid": "alert-1"},
    ).status_code == 401


def test_viewer_cannot_create_or_review_triage_tasks():
    gw = _load_gateway_main()
    gw.app.dependency_overrides[gw.get_current_user] = lambda: gw.CurrentUser(
        user_id="viewer-1",
        username="viewer",
        role="VIEWER",
        status="ACTIVE",
    )
    try:
        client = TestClient(gw.app)
        assert client.post(
            "/api/v1/alert-triage/tasks",
            json={"alertUuid": "alert-1"},
        ).status_code == 403
        assert client.post(
            "/api/v1/alert-triage/tasks/at-secret/reviews",
            json={"decision": "CONFIRMED"},
        ).status_code == 403
    finally:
        gw.app.dependency_overrides.clear()


def test_triage_terminal_text_explains_verdict_evidence_and_manual_actions():
    gw = _load_gateway_main()

    text = gw._triage_terminal_text(
        "at-terminal-1",
        "DONE",
        {
            "result": {
                "verdict": "true_positive",
                "confidence": 0.95,
                "summary": "检测到 WebShell 命令执行。",
                "xdr_evidence_refs": [
                    {"source": "alert", "uuid": "alert-1"},
                    {"source": "endpoint_log", "uuid": "log-1"},
                ],
                "recommended_actions": [{"action": "人工隔离主机"}],
            }
        },
    )

    assert "真实攻击（true_positive）" in text
    assert "95%" in text
    assert "alert:alert-1" in text
    assert "endpoint_log:log-1" in text
    assert "人工隔离主机" in text
    assert "未执行隔离" in text


def test_triage_terminal_text_uses_alert_wording_for_failed_and_cancelled_tasks():
    gw = _load_gateway_main()

    failed = gw._triage_terminal_text(
        "at-failed-1",
        "FAILED",
        None,
        phase="MAKE_DECISION",
        detail="模型响应格式错误",
    )
    cancelled = gw._triage_terminal_text("at-cancelled-1", "CANCELLED", None)

    assert failed == (
        "告警研判任务 at-failed-1 执行失败，停止在 MAKE_DECISION 阶段。"
        " 原因：模型响应格式错误 你可以查看执行轨迹定位失败步骤。"
    )
    assert cancelled == "告警研判任务 at-cancelled-1 已取消。"
    assert "渗透测试" not in failed + cancelled


@pytest.mark.asyncio
async def test_create_triage_returns_without_waiting_for_run(monkeypatch):
    gw = _load_gateway_main()
    scheduled = []
    inserts = []

    monkeypatch.setattr(gw, "_execute", lambda sql, params=(): inserts.append((sql, params)) or 1)
    rows = [None, {
        "task_id": "at-created",
        "target": "alert-1",
        "status": "PENDING",
        "created_at": "2026-08-03T07:00:00Z",
    }]
    monkeypatch.setattr(gw, "_get_task_row", lambda _task_id: rows.pop(0) if len(rows) > 1 else rows[0])

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


@pytest.mark.asyncio
async def test_create_triage_without_auto_start_registers_but_does_not_schedule(monkeypatch):
    gw = _load_gateway_main()
    scheduled = []
    monkeypatch.setattr(gw, "_execute", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(gw, "_get_task_row", lambda task_id: {
        "task_id": task_id,
        "target": "alert-1",
        "status": "PENDING",
    })
    async def register_only(*_args, **_kwargs):
        return {"status": "PENDING"}

    monkeypatch.setattr(gw, "_orch", register_only)
    monkeypatch.setattr(gw.asyncio, "create_task", lambda coro: scheduled.append(coro))

    task = await gw._create_alert_triage_task_impl(
        gw.CreateAlertTriageRequest(alert_uuid="alert-1"),
        task_id="at-deterministic",
        auto_start=False,
    )

    assert task["taskId"] == "at-deterministic"
    assert scheduled == []


@pytest.mark.asyncio
async def test_create_triage_failure_raises_and_marks_task_failed(monkeypatch):
    gw = _load_gateway_main()
    updates = []
    monkeypatch.setattr(gw, "_execute", lambda sql, params=(): updates.append((sql, params)) or 1)
    monkeypatch.setattr(gw, "_get_task_row", lambda task_id: {
        "task_id": task_id,
        "target": "alert-1",
        "status": "PENDING",
    })

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("orchestrator unavailable")

    monkeypatch.setattr(gw, "_orch", unavailable)

    with pytest.raises(gw.HTTPException) as exc:
        await gw._create_alert_triage_task_impl(
            gw.CreateAlertTriageRequest(alert_uuid="alert-1"),
            task_id="at-failed",
        )

    assert exc.value.status_code == 502
    assert any("status = 'FAILED'" in sql for sql, _params in updates)


@pytest.mark.asyncio
async def test_list_triage_enriches_all_terminal_tasks_without_first_page_cap(monkeypatch):
    gw = _load_gateway_main()
    rows = [
        {
            "task_id": f"at-{index}",
            "target": f"alert-{index}",
            "status": "DONE",
            "created_at": f"2026-08-03T07:{index:02d}:00Z",
        }
        for index in range(45)
    ]
    requested: list[str] = []
    monkeypatch.setattr(gw, "_query", lambda *_args, **_kwargs: rows)

    async def batch_states(task_ids):
        requested.extend(task_ids)
        return {
            task_id: {"task_id": task_id, "status": "DONE", "result": {"verdict": "suspicious"}}
            for task_id in task_ids
        }

    monkeypatch.setattr(gw, "_get_alert_triage_states", batch_states)
    response = await gw.list_alert_triage_tasks(limit=45)

    assert len(requested) == 45
    assert len(response["data"]) == 45
    assert all(task["verdict"] == "suspicious" for task in response["data"])


def test_operator_review_is_persisted_as_immutable_audit_record(monkeypatch):
    gw = _load_gateway_main()
    writes = []
    audits = []
    gw._ALERT_TRIAGE_REVIEW_SCHEMA_READY = False
    monkeypatch.setattr(gw, "_get_task_row", lambda _task_id: {"task_id": "at-1", "status": "DONE"})
    monkeypatch.setattr(gw, "_execute", lambda sql, params=(): writes.append((sql, params)) or 1)

    def query(sql, params=()):
        if "WHERE review_id" not in sql:
            return []
        values = next(item[1] for item in writes if "INSERT INTO tg_alert_triage_review" in item[0])
        return [{
            "review_id": values[0],
            "task_id": values[1],
            "reviewer_user_id": values[2],
            "reviewer_username": values[3],
            "decision": values[4],
            "human_verdict": values[5],
            "notes": values[6],
            "selected_actions": values[7],
            "created_at": "2026-08-08T08:00:00Z",
        }]

    monkeypatch.setattr(gw, "_query", query)
    monkeypatch.setattr(gw, "_record_audit", lambda *args: audits.append(args))
    user = gw.CurrentUser("operator-1", "operator", "OPERATOR", "ACTIVE")

    response = gw.create_alert_triage_review(
        "at-1",
        gw.AlertTriageReviewRequest(
            decision="OVERRIDDEN",
            humanVerdict="true_positive",
            notes="端点日志已由人工复核",
            selectedActions=["隔离主机"],
        ),
        user,
    )

    assert response["data"]["decision"] == "OVERRIDDEN"
    assert response["data"]["humanVerdict"] == "true_positive"
    assert response["data"]["selectedActions"] == ["隔离主机"]
    assert any("INSERT INTO tg_alert_triage_review" in sql for sql, _params in writes)
    assert audits[0][0] == "ALERT_TRIAGE_REVIEWED"
