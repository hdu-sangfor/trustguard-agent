import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest


def _load_modules():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        models = importlib.import_module("app.models")
        sm = importlib.import_module("app.core.state_machine")
        return models, sm
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


@pytest.mark.asyncio
async def test_phase_gate_blocked_event_is_deduped(monkeypatch):
    models, sm = _load_modules()
    emitted = []

    async def _fake_emit(event):
        emitted.append(event)

    monkeypatch.setattr(sm, "_emit", _fake_emit)
    state = models.TaskState(task_id="t-gate", name="n", target="http://x")
    state.current_phase = models.Phase.THREAT_MODEL

    await sm._emit_phase_gate_blocked(
        state,
        requested_phase="VULN_SCAN",
        reason="missing framework target",
        missing=["framework_target"],
    )
    await sm._emit_phase_gate_blocked(
        state,
        requested_phase="VULN_SCAN",
        reason="missing framework target",
        missing=["framework_target"],
    )

    assert len(emitted) == 1
    assert int((state.target_context or {}).get("_phase_gate_blocked_suppressed_count") or 0) == 1
