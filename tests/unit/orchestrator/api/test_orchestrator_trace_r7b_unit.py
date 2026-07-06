"""
R7b：轨迹 API 分页、execution 字段裁剪、plan 裁剪与脱敏。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


@pytest.fixture
def orch_trace_env(monkeypatch):
    monkeypatch.delenv("ORCH_TRACE_API_TOKEN", raising=False)
    monkeypatch.delenv("ORCH_TRACE_REDACT_SENSITIVE", raising=False)
    import app.main as main_mod
    from app.core.task_store import InMemoryTaskStore
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ORCH_TRACE_REDACT_SENSITIVE", "true")
    store = InMemoryTaskStore()
    monkeypatch.setattr(main_mod, "_TASK_STORE", store)
    main_mod._TASKS.clear()
    return TestClient(main_mod.app), main_mod, store


def test_executions_paging_and_fields(orch_trace_env):
    from app.models import TaskState

    client, main_mod, store = orch_trace_env
    main_mod._TASKS["tp"] = TaskState("tp", "n", "http://example.test")
    asyncio.run(store.register_execution_start("e-old", "tp", "s1", todo_id=None))
    asyncio.run(asyncio.sleep(0.002))
    asyncio.run(store.register_execution_start("e-new", "tp", "s2", todo_id=None))

    r0 = client.get("/v1/orchestrator/tasks/tp/executions?limit=1&offset=0")
    assert r0.status_code == 200
    b0 = r0.json()
    assert b0["total"] == 2
    assert b0["has_more"] is True
    assert b0["executions"][0]["request_id"] == "e-new"

    r1 = client.get("/v1/orchestrator/tasks/tp/executions?limit=1&offset=1")
    assert r1.json()["executions"][0]["request_id"] == "e-old"

    rf = client.get("/v1/orchestrator/tasks/tp/executions?fields=request_id,status")
    row = rf.json()["executions"][0]
    assert set(row.keys()) == {"request_id", "status"}


def test_prepare_tactical_incremental_section_api_filters_keys(monkeypatch):
    monkeypatch.setenv("ORCH_TRACE_REDACT_SENSITIVE", "true")
    from app.orchestrator_trace import prepare_tactical_incremental_section_api

    raw = [{"kind": "k", "summary": "Bearer secret-token", "noise": 1}]
    out = prepare_tactical_incremental_section_api(raw, apply_redact=True)
    assert len(out) == 1
    assert out[0].get("kind") == "k"
    assert "noise" not in out[0]
    assert "Bearer [REDACTED]" in str(out[0].get("summary"))


def test_unknown_execution_fields_400(orch_trace_env):
    from app.models import TaskState

    client, main_mod, _store = orch_trace_env
    main_mod._TASKS["tx"] = TaskState("tx", "n", "http://example.test")
    r = client.get("/v1/orchestrator/tasks/tx/executions?fields=request_id,bogus")
    assert r.status_code == 400
    assert "UNKNOWN_EXECUTION_FIELDS" in r.json()["detail"]["code"]


def test_artifact_ref_redacts_bearer(orch_trace_env):
    from app.models import TaskState

    client, main_mod, store = orch_trace_env
    main_mod._TASKS["tr"] = TaskState("tr", "n", "http://example.test")
    asyncio.run(store.register_execution_start("ex", "tr", "sk", todo_id=None))
    asyncio.run(
        store.register_execution_finish(
            "ex",
            "tr",
            "DONE",
            artifact_ref='{"Authorization":"Bearer supersecret"}',
        )
    )
    r = client.get("/v1/orchestrator/tasks/tr/executions")
    ar = r.json()["executions"][0]["artifact_ref"]
    assert "supersecret" not in ar
    assert "REDACTED" in ar


def test_plan_omit_content_and_strip_chunk_refs(orch_trace_env):
    from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY
    from app.models import TaskState

    client, main_mod, _store = orch_trace_env
    st = TaskState("tpl", "n", "http://example.test")
    st.target_context[LATEST_PLAN_LIST_CONTEXT_KEY] = {
        "schema_version": "plan-v1",
        "task_id": "tpl",
        "items": [
            {
                "plan_content": "x" * 50,
                "context_chunk_refs": [{"chunk_id": "z"}],
            }
        ],
    }
    main_mod._TASKS["tpl"] = st

    r = client.get(
        "/v1/orchestrator/tasks/tpl/trace/plan"
        "?plan_omit_plan_content=true&plan_include_chunk_refs=false",
    )
    p = r.json()["plan"]
    assert p["latest_plan_list"]["items"][0]["plan_content"] == "[omitted]"
    assert "context_chunk_refs" not in p["latest_plan_list"]["items"][0]
    assert p["context_chunk_refs_flat"] == []


def test_plan_validation_error_dropped_when_disabled(orch_trace_env):
    from app.core.plan_business_validate import PLAN_LIST_VALIDATION_ERROR_CONTEXT_KEY
    from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY
    from app.models import TaskState

    client, main_mod, _store = orch_trace_env
    st = TaskState("tv", "n", "http://example.test")
    st.target_context[LATEST_PLAN_LIST_CONTEXT_KEY] = {"items": []}
    st.target_context[PLAN_LIST_VALIDATION_ERROR_CONTEXT_KEY] = {"code": "E1"}
    main_mod._TASKS["tv"] = st

    r_all = client.get("/v1/orchestrator/tasks/tv/trace/plan")
    assert "plan_list_validation_error" in r_all.json()["plan"]

    r_no = client.get("/v1/orchestrator/tasks/tv/trace/plan?include_validation_error=false")
    assert "plan_list_validation_error" not in r_no.json()["plan"]
