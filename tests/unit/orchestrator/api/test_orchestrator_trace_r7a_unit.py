"""
R7a：/v1/orchestrator/tasks/{id}/trace* 与按 task 列举 executions。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from tests.orchestrator_test_env import orchestrator_sys_path_isolated


@pytest.fixture
def orch_trace_env(monkeypatch):
    monkeypatch.delenv("ORCH_TRACE_API_TOKEN", raising=False)
    with orchestrator_sys_path_isolated():
        import app.main as main_mod
        from app.core.task_store import InMemoryTaskStore, TaskRecord
        from fastapi.testclient import TestClient

        store = InMemoryTaskStore()
        monkeypatch.setattr(main_mod, "_TASK_STORE", store)
        main_mod._TASKS.clear()
        yield TestClient(main_mod.app), main_mod, store


def test_trace_memory_plan_and_executions(orch_trace_env):
    from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY
    from app.models import TaskState

    client, main_mod, store = orch_trace_env
    st = TaskState("t-trace-1", "n", "http://example.test")
    st.target_context[LATEST_PLAN_LIST_CONTEXT_KEY] = {
        "schema_version": "plan-v1",
        "task_id": "t-trace-1",
        "items": [
            {
                "context_chunk_refs": [{"schema_version": "plan-v1", "chunk_id": "chk-aa"}],
            }
        ],
    }
    st.target_context["_tactical_incremental_artifacts"] = [
        {"kind": "finding", "summary": "admin", "skill_id": "nmap"},
    ]
    main_mod._TASKS["t-trace-1"] = st
    asyncio.run(store.register_execution_start("req-a", "t-trace-1", "http-enum", todo_id=None))

    r = client.get("/v1/orchestrator/tasks/t-trace-1/trace")
    assert r.status_code == 200
    body = r.json()
    assert body["context_source"] == "memory"
    assert body["plan"]["context_chunk_refs_flat"][0]["chunk_id"] == "chk-aa"
    assert body["compile"]["persisted"] is False
    assert len(body["executions"]) == 1
    assert body["executions"][0]["request_id"] == "req-a"
    pg = body["executions_paging"]
    assert pg["total"] == 1 and pg["has_more"] is False and pg["next_offset"] is None
    ti = body.get("tactical_incremental_artifacts")
    assert isinstance(ti, list) and len(ti) == 1
    assert ti[0].get("kind") == "finding"
    assert ti[0].get("skill_id") == "nmap"


@pytest.mark.asyncio
async def test_trace_checkpoint_when_not_in_memory(orch_trace_env, monkeypatch):
    from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY
    from app.core.task_store import TaskRecord

    client, main_mod, store = orch_trace_env

    async def _fake_load(_tid: str):
        return {
            "target_context": {
                LATEST_PLAN_LIST_CONTEXT_KEY: {"schema_version": "plan-v1", "task_id": "t-ck", "items": []},
            }
        }

    monkeypatch.setattr(main_mod, "load_checkpoint_remote", _fake_load)
    await store.create_task(
        TaskRecord(task_id="t-ck", name="n", target="http://example.test"),
    )
    r = client.get("/v1/orchestrator/tasks/t-ck/trace/plan")
    assert r.status_code == 200
    assert r.json()["context_source"] == "checkpoint"
    assert r.json()["plan"]["latest_plan_list"]["task_id"] == "t-ck"


def test_trace_subresources(orch_trace_env):
    from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY
    from app.models import TaskState

    client, main_mod, _store = orch_trace_env
    st = TaskState("t-sub", "n", "http://example.test")
    st.target_context[LATEST_PLAN_LIST_CONTEXT_KEY] = {"items": []}
    main_mod._TASKS["t-sub"] = st

    p = client.get("/v1/orchestrator/tasks/t-sub/trace/plan")
    assert p.status_code == 200
    c = client.get("/v1/orchestrator/tasks/t-sub/trace/compile")
    assert c.status_code == 200
    assert c.json()["compile"]["persisted"] is False


def test_trace_token_when_configured(orch_trace_env, monkeypatch):
    monkeypatch.setenv("ORCH_TRACE_API_TOKEN", "tok-r7a")
    client, main_mod, _store = orch_trace_env
    from app.models import TaskState

    main_mod._TASKS["t-tok"] = TaskState("t-tok", "n", "http://example.test")
    r0 = client.get("/v1/orchestrator/tasks/t-tok/trace")
    assert r0.status_code == 401
    r1 = client.get(
        "/v1/orchestrator/tasks/t-tok/trace",
        headers={"Authorization": "Bearer tok-r7a"},
    )
    assert r1.status_code == 200


def test_list_executions_404(orch_trace_env):
    client, _main_mod, _store = orch_trace_env
    r = client.get("/v1/orchestrator/tasks/nonexistent-task-xyz/executions")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_in_memory_list_executions_order():
    with orchestrator_sys_path_isolated():
        from app.core.task_store import InMemoryTaskStore

        s = InMemoryTaskStore()
        await s.register_execution_start("r-old", "tx", "a", todo_id=None)
        await asyncio.sleep(0.002)
        await s.register_execution_start("r-new", "tx", "b", todo_id=None)
        rows = await s.list_executions_for_task("tx", limit=10)
        assert [x.request_id for x in rows] == ["r-new", "r-old"]
