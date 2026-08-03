import importlib
import sys

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
