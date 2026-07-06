import importlib
import sys
from datetime import datetime

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


def test_reset_phase_clock_for_resume_uses_current_time(monkeypatch):
    main, models = _load_main_module()
    monkeypatch.setenv("ORCH_PHASE_WALL_CLOCK_LIMIT_SEC", "321")

    state = models.TaskState(task_id="t-resume-clock", name="n", target="http://example.test/")
    state.current_phase = models.Phase.THREAT_MODEL
    state.phase_start_at = datetime(2020, 1, 1, 0, 0, 0)

    before = datetime.utcnow()
    main._reset_phase_clock_for_resume(state)
    after = datetime.utcnow()

    assert state.current_phase == models.Phase.THREAT_MODEL
    assert state.phase_start_at is not None
    assert before <= state.phase_start_at <= after
    assert state.current_phase_duration_limit_sec == 321
