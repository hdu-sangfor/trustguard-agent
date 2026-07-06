import importlib
import sys

import pytest

from tests.paths import REPO_ROOT


def _load_main_module():
    orch_root = str(REPO_ROOT / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        main = importlib.import_module("app.main")
        models = importlib.import_module("app.models")
        return main, models
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)


@pytest.mark.asyncio
async def test_run_loop_duration_limit_emits_visible_pause_reason(monkeypatch):
    main, models = _load_main_module()
    emitted = []
    checkpoints = []

    async def fake_emit(event):
        emitted.append(event)

    async def fake_save_checkpoint(**kwargs):
        checkpoints.append(kwargs)

    monkeypatch.setattr(main, "_emit_trace", fake_emit)
    monkeypatch.setattr(main, "save_checkpoint_remote", fake_save_checkpoint)

    state = models.TaskState(task_id="t-run-duration", name="n", target="http://example.test/")
    state.status = models.TaskStatus.RUNNING

    ticks = iter([0.0, 1.1])
    monkeypatch.setattr(main.time, "perf_counter", lambda: next(ticks))

    await main._run_loop(state, max_ticks=1, max_duration_seconds=1)

    assert state.status == models.TaskStatus.PAUSED
    assert state.stop_requested is True
    assert checkpoints[-1]["status"] == "PAUSED"
    pause_events = [e for e in emitted if e.event_type == "TASK_PAUSED"]
    assert pause_events
    assert pause_events[-1].payload["reason_code"] == "RUN_DURATION_LIMIT"
    assert pause_events[-1].payload["max_duration_seconds"] == 1
    assert "单次运行时长达到上限" in pause_events[-1].payload["reason"]


def test_default_run_duration_is_long_enough_for_scanners(monkeypatch):
    main, _models = _load_main_module()

    monkeypatch.delenv("ORCH_DEFAULT_RUN_MAX_DURATION_SECONDS", raising=False)
    assert main._default_run_max_duration_seconds() == 900

    monkeypatch.setenv("ORCH_DEFAULT_RUN_MAX_DURATION_SECONDS", "0")
    assert main._default_run_max_duration_seconds() == 0
