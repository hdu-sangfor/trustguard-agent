import importlib
import sys
from datetime import datetime, timedelta

import pytest

from tests.paths import REPO_ROOT


def _load_modules():
    orch_root = str(REPO_ROOT / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        models = importlib.import_module("app.models")
        sm = importlib.import_module("app.core.state_machine")
        guard = importlib.import_module("app.core.phase_transition_guard")
        return models, sm, guard
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)


def test_web_target_requires_real_vuln_scan_before_report():
    models, _sm, guard = _load_modules()
    state = models.TaskState(task_id="t-vuln-gate", name="n", target="http://host.docker.internal:8080/")
    state.coverage_attempted = [
        {"target": state.target, "skill_id": "katana"},
        {"target": state.target, "skill_id": "dirsearch"},
        {"target": state.target, "skill_id": "http-enum"},
        {"target": state.target, "skill_id": "curl-raw"},
    ]

    assert guard.needs_vuln_scan_before_report(state) is True

    state.coverage_attempted.append({"target": state.target, "skill_id": "nuclei"})
    assert guard.needs_vuln_scan_before_report(state) is False


def test_failed_vuln_scanner_does_not_satisfy_coverage():
    models, _sm, guard = _load_modules()
    state = models.TaskState(task_id="t-vuln-failed", name="n", target="http://example.test/")
    state.coverage_attempted = [{"target": state.target, "skill_id": "nuclei", "status": "FAILED"}]

    assert guard.needs_vuln_scan_before_report(state) is True


def test_legacy_vuln_scanner_coverage_without_status_still_counts():
    models, _sm, guard = _load_modules()
    state = models.TaskState(task_id="t-vuln-legacy", name="n", target="http://example.test/")
    state.coverage_attempted = [{"target": state.target, "skill_id": "nuclei"}]

    assert guard.needs_vuln_scan_before_report(state) is False


def test_dispatcher_prepare_does_not_satisfy_vuln_scan_coverage():
    models, _sm, guard = _load_modules()
    state = models.TaskState(task_id="t-dispatcher-only", name="n", target="http://example.test/")
    state.coverage_attempted = [{"target": state.target, "skill_id": "dispatcher"}]

    assert guard.needs_vuln_scan_before_report(state) is True


def test_vuln_scan_to_report_blocked_without_scanner_coverage():
    models, _sm, guard = _load_modules()
    state = models.TaskState(task_id="t-vuln-report", name="n", target="http://example.test/")
    state.current_phase = models.Phase.VULN_SCAN
    state.coverage_attempted = [{"target": state.target, "skill_id": "dispatcher"}]

    decision = guard.guard_next_phase(state, models.Phase.REPORT)

    assert decision.allow is False
    assert decision.blocked is True
    assert decision.phase == models.Phase.VULN_SCAN
    assert "vuln_scan_coverage" in ",".join(decision.missing)


def test_vuln_scan_to_report_allowed_after_scanner_coverage():
    models, _sm, guard = _load_modules()
    state = models.TaskState(task_id="t-vuln-report-ok", name="n", target="http://example.test/")
    state.current_phase = models.Phase.VULN_SCAN
    state.coverage_attempted = [{"target": state.target, "skill_id": "nuclei"}]

    decision = guard.guard_next_phase(state, models.Phase.REPORT)

    assert decision.allow is True
    assert decision.phase == models.Phase.REPORT


@pytest.mark.asyncio
async def test_report_finish_gate_blocks_without_scanner_coverage(monkeypatch):
    models, sm, _guard = _load_modules()
    emitted = []

    async def _fake_emit(event):
        emitted.append(event)

    monkeypatch.setattr(sm, "_emit", _fake_emit)
    state = models.TaskState(task_id="t-report-gate", name="n", target="http://example.test/")
    state.status = models.TaskStatus.RUNNING
    state.current_phase = models.Phase.REPORT
    state.coverage_attempted = [{"target": state.target, "skill_id": "curl-raw"}]

    blocked = await sm._enforce_report_finish_gate(
        state,
        {"gap_sample": [{"target": state.target, "skill_id": "nuclei"}]},
    )

    assert blocked is True
    assert state.current_phase == models.Phase.VULN_SCAN
    assert state.target_context.get("_report_blocked_missing_vuln_scan") is True
    assert any(e.event_type == "REPORT_GATE_BLOCKED" for e in emitted)


@pytest.mark.asyncio
async def test_wall_clock_budget_cannot_skip_vuln_scanner_coverage(monkeypatch):
    models, sm, _guard = _load_modules()
    emitted = []

    async def _fake_emit(event):
        emitted.append(event)

    monkeypatch.setattr(sm, "_emit", _fake_emit)
    state = models.TaskState(task_id="t-wall-budget", name="n", target="http://example.test/")
    state.status = models.TaskStatus.RUNNING
    state.current_phase = models.Phase.VULN_SCAN
    state.phase_start_at = datetime.utcnow() - timedelta(seconds=10)
    state.current_phase_duration_limit_sec = 1
    state.coverage_attempted = [{"target": state.target, "skill_id": "curl-raw"}]

    await sm._maybe_enforce_phase_wall_clock_budget(state)

    assert state.current_phase == models.Phase.VULN_SCAN
    assert any(e.event_type == "PHASE_BUDGET_BLOCKED" for e in emitted)
    assert not any(e.event_type == "PHASE_BUDGET_EXCEEDED" for e in emitted)


@pytest.mark.asyncio
async def test_plan_round_cap_cannot_skip_vuln_scanner_coverage(monkeypatch):
    models, sm, _guard = _load_modules()
    emitted = []

    async def _fake_emit(event):
        emitted.append(event)

    monkeypatch.setattr(sm, "_emit", _fake_emit)
    monkeypatch.setenv("ORCH_MAX_PLAN_ROUNDS_PER_PHASE", "2")
    state = models.TaskState(task_id="t-round-cap", name="n", target="http://example.test/")
    state.status = models.TaskStatus.RUNNING
    state.current_phase = models.Phase.VULN_SCAN
    state.phase_plan_round_count = 2
    state.coverage_attempted = [{"target": state.target, "skill_id": "dispatcher"}]

    await sm._maybe_enforce_phase_plan_round_cap(state)

    assert state.current_phase == models.Phase.VULN_SCAN
    assert state.phase_plan_round_count == 2
    assert any(e.event_type == "PLAN_ROUND_CAP_BLOCKED" for e in emitted)
    assert not any(e.event_type == "PLAN_ROUND_CAP_EXCEEDED" for e in emitted)


@pytest.mark.asyncio
async def test_cost_budget_cannot_skip_vuln_scanner_coverage(monkeypatch):
    models, sm, _guard = _load_modules()
    emitted = []

    async def _fake_emit(event):
        emitted.append(event)

    monkeypatch.setattr(sm, "_emit", _fake_emit)
    monkeypatch.setenv("ORCH_PHASE_COST_BUDGET_USD", "0.01")
    state = models.TaskState(task_id="t-cost-budget", name="n", target="http://example.test/")
    state.status = models.TaskStatus.RUNNING
    state.current_phase = models.Phase.VULN_SCAN
    state.cumulative_cost_usd = 0.02
    state.coverage_attempted = [{"target": state.target, "skill_id": "http-enum"}]

    await sm._maybe_enforce_phase_cost_budget(state)

    assert state.current_phase == models.Phase.VULN_SCAN
    assert any(e.event_type == "COST_BUDGET_BLOCKED" for e in emitted)
    assert not any(e.event_type == "COST_BUDGET_EXCEEDED" for e in emitted)
