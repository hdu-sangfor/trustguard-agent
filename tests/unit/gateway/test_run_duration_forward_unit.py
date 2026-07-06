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


@pytest.mark.asyncio
async def test_gateway_run_lifecycle_does_not_override_orchestrator_duration_default(monkeypatch):
    gw = _load_gateway_main()
    captured = []

    def fake_get_task_row(_task_id):
        return {"task_id": "task-gw-duration", "name": "n", "target": "http://example.test/"}

    async def fake_restore(_row):
        return None

    async def fake_orch(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {"taskId": "task-gw-duration", "status": "RUNNING", "currentPhase": "RECON"}

    monkeypatch.setattr(gw, "_get_task_row", fake_get_task_row)
    monkeypatch.setattr(gw, "_best_effort_restore", fake_restore)
    monkeypatch.setattr(gw, "_orch", fake_orch)
    monkeypatch.setattr(gw, "_sync_task_state", lambda *_args, **_kwargs: None)

    res = await gw._run_lifecycle("task-gw-duration", "run", max_ticks=123)

    assert res["code"] == "0"
    params = captured[-1][2]["params"]
    assert params == {"max_ticks": 123}


@pytest.mark.asyncio
async def test_gateway_run_lifecycle_forwards_explicit_duration(monkeypatch):
    gw = _load_gateway_main()
    captured = []

    monkeypatch.setattr(
        gw,
        "_get_task_row",
        lambda _task_id: {"task_id": "task-gw-duration", "name": "n", "target": "http://example.test/"},
    )

    async def fake_restore(_row):
        return None

    async def fake_orch(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {"taskId": "task-gw-duration", "status": "RUNNING", "currentPhase": "RECON"}

    monkeypatch.setattr(gw, "_best_effort_restore", fake_restore)
    monkeypatch.setattr(gw, "_orch", fake_orch)
    monkeypatch.setattr(gw, "_sync_task_state", lambda *_args, **_kwargs: None)

    res = await gw._run_lifecycle(
        "task-gw-duration",
        "run",
        max_ticks=123,
        max_duration_seconds=1200,
    )

    assert res["code"] == "0"
    params = captured[-1][2]["params"]
    assert params == {"max_ticks": 123, "max_duration_seconds": 1200}
