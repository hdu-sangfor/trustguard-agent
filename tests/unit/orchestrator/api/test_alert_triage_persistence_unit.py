import asyncio
import importlib
import sys
from unittest.mock import AsyncMock

import pytest

from tests.paths import REPO_ROOT


def _load_orchestrator_main():
    orchestrator_root = str(REPO_ROOT / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orchestrator_root)
    try:
        return importlib.import_module("app.main")
    finally:
        if orchestrator_root in sys.path:
            sys.path.remove(orchestrator_root)


@pytest.mark.asyncio
async def test_alert_triage_state_restores_from_evidence_context(monkeypatch):
    orch = _load_orchestrator_main()
    from app.clients import evidence_client

    contexts = {}

    async def fake_put_context(task_id, context):
        contexts[task_id] = context

    async def fake_get_context(task_id):
        return contexts.get(task_id, {})

    monkeypatch.setattr(evidence_client, "put_context", fake_put_context)
    monkeypatch.setattr(evidence_client, "get_context", fake_get_context)

    state = {
        "task_id": "at-persisted",
        "alert_uuid": "alert-1",
        "status": "DONE",
        "result": {"verdict": "suspicious"},
    }
    await orch._persist_alert_triage_task(state)
    orch._alert_triage_results.clear()

    restored = await orch._load_persisted_alert_triage_task("at-persisted")

    assert restored == state


@pytest.mark.asyncio
async def test_alert_triage_create_is_idempotent_for_existing_task(monkeypatch):
    orch = _load_orchestrator_main()
    orch._alert_triage_results["at-idempotent"] = {
        "task_id": "at-idempotent",
        "alert_uuid": "alert-1",
        "status": "PENDING",
    }

    same = await orch.alert_triage_create_task(
        orch._AlertTriageCreatePayload(task_id="at-idempotent", alert_uuid="alert-1")
    )
    assert same["idempotent"] is True
    assert same["status"] == "PENDING"

    with pytest.raises(orch.HTTPException) as exc_info:
        await orch.alert_triage_create_task(
            orch._AlertTriageCreatePayload(task_id="at-idempotent", alert_uuid="alert-2")
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_alert_triage_run_returns_current_state_when_already_running(monkeypatch):
    orch = _load_orchestrator_main()
    task_id = "at-already-running"
    orch._alert_triage_results[task_id] = {
        "task_id": task_id,
        "alert_uuid": "alert-1",
        "status": "RUNNING",
    }
    lock = orch.asyncio.Lock()
    await lock.acquire()
    orch._alert_triage_run_locks[task_id] = lock

    result = await orch.alert_triage_run_task(task_id)

    assert result["already_running"] is True
    assert result["status"] == "RUNNING"
    lock.release()
    orch._alert_triage_run_locks.pop(task_id, None)


@pytest.mark.asyncio
async def test_alert_triage_run_honors_task_store_lock(monkeypatch):
    orch = _load_orchestrator_main()
    task_id = "at-store-locked"
    orch._alert_triage_results[task_id] = {
        "task_id": task_id,
        "alert_uuid": "alert-1",
        "status": "PENDING",
    }
    store = orch._TASK_STORE
    assert await store.acquire_task_lock(task_id, "another-orchestrator", ttl_seconds=60)

    async def should_not_run(_task):
        raise AssertionError("LLM triage must not run while another owner holds the lock")

    monkeypatch.setattr(orch, "run_alert_triage", should_not_run)
    result = await orch.alert_triage_run_task(task_id)

    assert result["already_running"] is True
    assert result["status"] == "PENDING"
    await store.release_task_lock(task_id, "another-orchestrator")


@pytest.mark.asyncio
async def test_recover_alert_triage_tasks_schedules_only_stale_running_tasks(monkeypatch):
    orch = _load_orchestrator_main()
    task_id = "at-recover-me"
    monkeypatch.setattr(orch, "_ALERT_TRIAGE_RECOVERY_GRACE_SECONDS", 60)
    monkeypatch.setattr(
        orch,
        "_load_persisted_alert_triage_task",
        AsyncMock(
            return_value={"task_id": task_id, "status": "RUNNING", "alert_uuid": "alert-1"}
        ),
    )
    monkeypatch.setattr(
        "app.clients.evidence_client.list_internal_tasks",
        AsyncMock(
            return_value=[
                {
                    "task_id": task_id,
                    "status": "RUNNING",
                    "updated_at": "2020-01-01T00:00:00Z",
                },
                {
                    "task_id": "at-fresh",
                    "status": "RUNNING",
                    "updated_at": "2999-01-01T00:00:00Z",
                },
                {
                    "task_id": "normal-task",
                    "status": "RUNNING",
                    "updated_at": "2020-01-01T00:00:00Z",
                },
            ]
        ),
    )
    scheduled: list[str] = []

    async def _run(task_id: str):
        scheduled.append(task_id)
        return {"task_id": task_id}

    monkeypatch.setattr(orch, "alert_triage_run_task", _run)
    await orch._recover_alert_triage_tasks()
    await asyncio.sleep(0)
    assert scheduled == [task_id]
